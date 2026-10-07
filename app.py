import streamlit as st
import pandas as pd
import re
import io
import csv
import os

from datetime import datetime, timedelta, time, date
from supabase import create_client
from dotenv import load_dotenv


# ============================================================
# PAGE SETUP
# ============================================================

st.set_page_config(
    page_title="Schedule & Labor Helper",
    page_icon="📊",
    layout="wide"
)

st.title("📊 Schedule & Labor Helper")

st.caption(
    "Analyze staffing, employee hours, projected labor, "
    "sales targets, and schedule issues."
)


# ============================================================
# ENVIRONMENT / SUPABASE
# ============================================================

load_dotenv()


@st.cache_resource
def get_supabase():

    supabase_url = os.getenv("SUPABASE_URL")
    service_role_key = os.getenv("SECRET_KEY")

    if not supabase_url:
        st.error(
            "SUPABASE_URL was not found in your .env file."
        )
        st.stop()

    if not service_role_key:
        st.error(
            "SUPABASE_KEY was not found "
            "in your .env file."
        )
        st.stop()

    return create_client(
        supabase_url,
        service_role_key
    )


supabase = get_supabase()


# ============================================================
# BASIC HELPERS
# ============================================================

def clean_text(value):

    if pd.isna(value):
        return ""

    return str(value).strip()


def normalize_name(value):

    return " ".join(
        clean_text(value)
        .lower()
        .split()
    )


def parse_time_string(value):

    try:
        return datetime.strptime(
            value.strip(),
            "%I:%M %p"
        ).time()

    except Exception:
        return None


def time_to_minutes(t):

    return (
        t.hour * 60
        + t.minute
    )


def minutes_to_label(minutes):

    minutes %= 1440

    hour = minutes // 60
    minute = minutes % 60

    dt = datetime.combine(
        datetime.today(),
        time(hour, minute)
    )

    return dt.strftime(
        "%I:%M %p"
    ).lstrip("0")


def format_time(t):

    if t is None:
        return ""

    return t.strftime(
        "%I:%M %p"
    ).lstrip("0")


def parse_date_value(value):

    if isinstance(value, datetime):
        return value.date()

    if isinstance(value, date):
        return value

    return pd.to_datetime(
        value
    ).date()


# ============================================================
# SHIFT HOURS
# ============================================================

def calculate_hours(
    start_time,
    end_time
):

    if (
        start_time is None
        or end_time is None
    ):
        return 0

    today = datetime.today().date()

    start = datetime.combine(
        today,
        start_time
    )

    end = datetime.combine(
        today,
        end_time
    )

    if end <= start:
        end += timedelta(days=1)

    return round(
        (
            end - start
        ).total_seconds()
        / 3600,
        2
    )


# ============================================================
# PROJECTED MEAL PERIODS
# ============================================================

def calculate_required_break_minutes(
    start_time,
    end_time
):

    hours = calculate_hours(
        start_time,
        end_time
    )

    if hours <= 6:
        return 0

    start_minutes = time_to_minutes(
        start_time
    )

    end_minutes = time_to_minutes(
        end_time
    )

    if end_minutes <= start_minutes:
        end_minutes += 1440

    # Projected meal rule used by this planning tool.
    if (
        start_minutes >= 13 * 60
        or start_minutes < 6 * 60
    ):
        break_minutes = 45

    else:
        break_minutes = 30

    if (
        start_minutes < 11 * 60
        and end_minutes > 19 * 60
    ):
        break_minutes += 20

    return break_minutes


# ============================================================
# FRACTIONAL STAFFING
# ============================================================

def employee_coverage_during_slot(
    start_time,
    end_time,
    slot_start,
    slot_end
):

    start = time_to_minutes(
        start_time
    )

    end = time_to_minutes(
        end_time
    )

    if end <= start:
        end += 1440

    overlap_start = max(
        start,
        slot_start
    )

    overlap_end = min(
        end,
        slot_end
    )

    overlap_minutes = max(
        0,
        overlap_end - overlap_start
    )

    slot_minutes = (
        slot_end - slot_start
    )

    if slot_minutes <= 0:
        return 0

    return round(
        overlap_minutes
        / slot_minutes,
        4
    )


# ============================================================
# EXTRACT SHIFTS FROM SCHEDULE CELL
# ============================================================

def extract_shifts_from_cell(
    cell_text,
    employee_name,
    schedule_date,
    schedule_section
):

    shifts = []

    text = clean_text(
        cell_text
    )

    if not text:
        return shifts

    time_pattern = re.compile(
        r"(\d{1,2}:\d{2}\s*[AP]M)"
        r"\s*-\s*"
        r"(\d{1,2}:\d{2}\s*[AP]M)",
        re.IGNORECASE
    )

    matches = list(
        time_pattern.finditer(text)
    )

    if not matches:
        return shifts

    known_departments = [
        "Front of House",
        "Back of House",
        "Leadership",
        "Training",
        "Marketing",
        "Delivery",
        "Beyond the Restaurant",
        "Other"
    ]

    for i, match in enumerate(matches):

        start_time = parse_time_string(
            match.group(1).upper()
        )

        end_time = parse_time_string(
            match.group(2).upper()
        )

        if (
            start_time is None
            or end_time is None
        ):
            continue

        previous_end = (
            matches[i - 1].end()
            if i > 0
            else 0
        )

        before_text = (
            text[
                previous_end:
                match.start()
            ]
            .strip()
        )

        next_start = (
            matches[i + 1].start()
            if i + 1 < len(matches)
            else len(text)
        )

        after_text = (
            text[
                match.end():
                next_start
            ]
            .strip()
        )

        shift_released = (
            "[Shift Released]" in before_text
            or
            "[Shift Released]" in after_text
        )

        before_text = (
            before_text
            .replace(
                "[Shift Released]",
                ""
            )
            .strip()
        )

        after_text = (
            after_text
            .replace(
                "[Shift Released]",
                ""
            )
            .strip()
        )

        department = schedule_section

        for dept in known_departments:

            if (
                dept.lower()
                in before_text.lower()
            ):
                department = dept
                break

        job_type = after_text

        if not job_type:
            job_type = department

        for dept in known_departments:

            if (
                job_type.lower()
                .startswith(
                    dept.lower()
                )
            ):
                job_type = (
                    job_type[
                        len(dept):
                    ]
                    .strip()
                )

        if not job_type:
            job_type = department

        shifts.append(
            {
                "employee":
                    employee_name,

                "date":
                    schedule_date,

                "department":
                    department,

                "job_type":
                    job_type,

                "start_time":
                    start_time,

                "end_time":
                    end_time,

                "hours":
                    calculate_hours(
                        start_time,
                        end_time
                    ),

                "shift_released":
                    shift_released
            }
        )

    return shifts


# ============================================================
# PARSE SCHEDULE
# ============================================================

@st.cache_data
def parse_schedule(file_bytes):

    all_shifts = []

    try:

        tables = pd.read_html(
            io.BytesIO(
                file_bytes
            )
        )

    except Exception as e:

        raise Exception(
            f"Could not read schedule file: {e}"
        )

    for table in tables:

        if (
            table.empty
            or table.shape[1] < 3
        ):
            continue

        first_cell = clean_text(
            table.iloc[
                0,
                0
            ]
        )

        schedule_section = (
            first_cell
            .replace(
                "[Status = Posted]",
                ""
            )
            .replace(
                "[Status = Not Started]",
                ""
            )
            .strip()
        )

        date_columns = {}

        for column_index in range(
            1,
            table.shape[1]
        ):

            header = clean_text(
                table.iloc[
                    0,
                    column_index
                ]
            )

            if not header:
                continue

            try:

                parsed_date = (
                    pd.to_datetime(
                        header,
                        errors="raise"
                    )
                    .date()
                )

                date_columns[
                    column_index
                ] = parsed_date

            except Exception:
                continue

        if not date_columns:
            continue

        for row_index in range(
            1,
            len(table)
        ):

            employee_name = clean_text(
                table.iloc[
                    row_index,
                    0
                ]
            )

            if not employee_name:
                continue

            employee_lower = (
                employee_name.lower()
            )

            if (
                employee_lower.startswith(
                    "total"
                )
                or employee_lower == "employee"
            ):
                continue

            for (
                column_index,
                schedule_date
            ) in date_columns.items():

                cell_text = clean_text(
                    table.iloc[
                        row_index,
                        column_index
                    ]
                )

                if not cell_text:
                    continue

                shifts = extract_shifts_from_cell(
                    cell_text,
                    employee_name,
                    schedule_date,
                    schedule_section
                )

                all_shifts.extend(
                    shifts
                )

    if not all_shifts:
        return pd.DataFrame()

    df = pd.DataFrame(
        all_shifts
    )

    df = (
        df
        .drop_duplicates(
            subset=[
                "employee",
                "date",
                "start_time",
                "end_time",
                "job_type"
            ]
        )
        .reset_index(
            drop=True
        )
    )

    return df


# ============================================================
# MERGE EMPLOYEE WORK INTERVALS
# ============================================================

def get_merged_work_intervals(
    employee_day_df
):

    intervals = []

    for _, row in (
        employee_day_df.iterrows()
    ):

        start = time_to_minutes(
            row["start_time"]
        )

        end = time_to_minutes(
            row["end_time"]
        )

        if end <= start:
            end += 1440

        intervals.append(
            (
                start,
                end
            )
        )

    if not intervals:
        return []

    intervals.sort()

    merged = [
        intervals[0]
    ]

    for start, end in intervals[1:]:

        previous_start, previous_end = (
            merged[-1]
        )

        if start <= previous_end:

            merged[-1] = (
                previous_start,
                max(
                    previous_end,
                    end
                )
            )

        else:

            merged.append(
                (
                    start,
                    end
                )
            )

    return merged


def absolute_minutes_to_time(
    minutes
):

    minutes %= 1440

    return time(
        minutes // 60,
        minutes % 60
    )


# ============================================================
# EMPLOYEE DAILY HOURS
# ============================================================

def calculate_employee_day_summary(
    employee_day_df
):

    merged = (
        get_merged_work_intervals(
            employee_day_df
        )
    )

    if not merged:

        return {
            "pre_break_hours": 0,
            "break_minutes": 0,
            "post_break_hours": 0
        }

    total_pre_break_minutes = 0
    total_break_minutes = 0

    for start, end in merged:

        total_pre_break_minutes += (
            end - start
        )

        start_time = (
            absolute_minutes_to_time(
                start
            )
        )

        end_time = (
            absolute_minutes_to_time(
                end
            )
        )

        total_break_minutes += (
            calculate_required_break_minutes(
                start_time,
                end_time
            )
        )

    pre_break_hours = (
        total_pre_break_minutes
        / 60
    )

    post_break_hours = (
        (
            total_pre_break_minutes
            - total_break_minutes
        )
        / 60
    )

    return {
        "pre_break_hours":
            round(
                pre_break_hours,
                2
            ),

        "break_minutes":
            total_break_minutes,

        "post_break_hours":
            round(
                max(
                    post_break_hours,
                    0
                ),
                2
            )
    }


def build_employee_hours_table(
    schedule_df
):

    records = []

    for (
        employee,
        employee_df
    ) in schedule_df.groupby(
        "employee"
    ):

        for (
            schedule_date,
            day_df
        ) in employee_df.groupby(
            "date"
        ):

            summary = (
                calculate_employee_day_summary(
                    day_df
                )
            )

            records.append(
                {
                    "employee":
                        employee,

                    "date":
                        schedule_date,

                    "pre_break_hours":
                        summary[
                            "pre_break_hours"
                        ],

                    "break_minutes":
                        summary[
                            "break_minutes"
                        ],

                    "post_break_hours":
                        summary[
                            "post_break_hours"
                        ]
                }
            )

    return pd.DataFrame(
        records
    )


# ============================================================
# STAFFING TABLE
# ============================================================

def build_staffing_table(
    day_df,
    interval_minutes
):

    if day_df.empty:
        return pd.DataFrame()

    starts = []
    ends = []

    for _, row in (
        day_df.iterrows()
    ):

        start = time_to_minutes(
            row["start_time"]
        )

        end = time_to_minutes(
            row["end_time"]
        )

        if end <= start:
            end += 1440

        starts.append(start)
        ends.append(end)

    earliest = (
        min(starts)
        // interval_minutes
        * interval_minutes
    )

    latest = (
        (
            max(ends)
            + interval_minutes
            - 1
        )
        // interval_minutes
        * interval_minutes
    )

    job_types = sorted(
        day_df[
            "job_type"
        ]
        .dropna()
        .unique()
    )

    rows = []
    current = earliest

    while current < latest:

        slot_end = (
            current
            + interval_minutes
        )

        row_data = {
            "Time":
                minutes_to_label(
                    current
                )
        }

        employee_coverage = {}

        for job_type in job_types:

            job_df = (
                day_df[
                    day_df[
                        "job_type"
                    ]
                    == job_type
                ]
            )

            job_coverage = 0

            for _, shift in (
                job_df.iterrows()
            ):

                coverage = (
                    employee_coverage_during_slot(
                        shift[
                            "start_time"
                        ],
                        shift[
                            "end_time"
                        ],
                        current,
                        slot_end
                    )
                )

                job_coverage += (
                    coverage
                )

                employee = (
                    shift[
                        "employee"
                    ]
                )

                employee_coverage[
                    employee
                ] = (
                    employee_coverage.get(
                        employee,
                        0
                    )
                    + coverage
                )

            row_data[
                job_type
            ] = round(
                job_coverage,
                2
            )

        row_data[
            "Total Employees"
        ] = round(
            sum(
                min(
                    coverage,
                    1.0
                )
                for coverage
                in employee_coverage.values()
            ),
            2
        )

        rows.append(
            row_data
        )

        current += (
            interval_minutes
        )

    return pd.DataFrame(
        rows
    )


# ============================================================
# HOURS PIVOT
# ============================================================

def create_hours_pivot(
    hours_df,
    value_column
):

    pivot = (
        hours_df
        .pivot_table(
            index="employee",
            columns="date",
            values=value_column,
            aggfunc="sum",
            fill_value=0
        )
    )

    pivot[
        "Weekly Total"
    ] = pivot.sum(
        axis=1
    )

    rename_columns = {}

    for column in pivot.columns:

        if column == "Weekly Total":
            continue

        rename_columns[
            column
        ] = column.strftime(
            "%a %m/%d"
        )

    pivot = pivot.rename(
        columns=rename_columns
    )

    return pivot.sort_values(
        "Weekly Total",
        ascending=False
    )


# ============================================================
# SUPABASE DATABASE FUNCTIONS
# ============================================================

@st.cache_data(ttl=300)
def get_labor_database():

    employee_response = (
        supabase
        .table("labor_employees")
        .select("id, employee_name, active, compensation_type")
        .execute()
    )

    rate_response = (
        supabase
        .table("labor_pay_rates")
        .select(
            "id, employee_id, hourly_rate, "
            "effective_date, created_at"
        )
        .execute()
    )

    salary_response = (
        supabase
        .table("labor_salaries")
        .select(
            "id, employee_id, annual_salary, "
            "effective_date, created_at"
        )
        .execute()
    )

    alias_response = (
        supabase
        .table("labor_employee_aliases")
        .select(
            "id, employee_id, alias_name, created_at"
        )
        .execute()
    )

    return (
        employee_response.data or [],
        rate_response.data or [],
        salary_response.data or [],
        alias_response.data or []
    )

def clear_labor_cache():
    get_labor_database.clear()

def find_employee_by_name(
    employee_name,
    employees_db,
    aliases_db
):

    normalized_name = normalize_name(
        employee_name
    )

    # First try the employee's real name
    for employee in employees_db:

        if (
            normalize_name(
                employee["employee_name"]
            )
            == normalized_name
        ):
            return employee

    # If no direct match, check aliases
    for alias in aliases_db:

        if (
            normalize_name(
                alias["alias_name"]
            )
            == normalized_name
        ):

            employee_id = int(
                alias["employee_id"]
            )

            for employee in employees_db:

                if (
                    int(employee["id"])
                    == employee_id
                ):
                    return employee

    return None


def add_employee_alias(
    employee_id,
    alias_name
):

    alias_name = alias_name.strip()

    if not alias_name:
        raise ValueError(
            "Alias name is required."
        )

    response = (
        supabase
        .table("labor_employee_aliases")
        .insert(
            {
                "employee_id": int(
                    employee_id
                ),
                "alias_name": alias_name
            }
        )
        .execute()
    )

    if not response.data:
        raise Exception(
            "Alias could not be saved."
        )

    clear_labor_cache()

    return response.data[0]


def delete_employee_alias(alias_id):

    (
        supabase
        .table("labor_employee_aliases")
        .delete()
        .eq(
            "id",
            int(alias_id)
        )
        .execute()
    )

    clear_labor_cache()


def get_employee_rate_history(employee_id):

    response = (
        supabase
        .table("labor_pay_rates")
        .select("id, employee_id, hourly_rate, effective_date, created_at")
        .eq("employee_id", int(employee_id))
        .order("effective_date", desc=True)
        .order("created_at", desc=True)
        .execute()
    )

    return response.data or []


def get_employee_salary_history(employee_id):

    response = (
        supabase
        .table("labor_salaries")
        .select("id, employee_id, annual_salary, effective_date, created_at")
        .eq("employee_id", int(employee_id))
        .order("effective_date", desc=True)
        .order("created_at", desc=True)
        .execute()
    )

    return response.data or []


def get_effective_value(employee_id, target_date, records, value_column):

    target_date = parse_date_value(target_date)
    valid = []

    for record in records:
        if int(record["employee_id"]) != int(employee_id):
            continue

        effective_date = parse_date_value(record["effective_date"])

        if effective_date <= target_date:
            valid.append(
                (
                    effective_date,
                    record.get("created_at", ""),
                    float(record[value_column])
                )
            )

    if not valid:
        return None

    valid.sort(
        key=lambda x: (x[0], x[1]),
        reverse=True
    )

    return valid[0][2]


def get_rate_for_date(employee_id, schedule_date, all_rates):
    return get_effective_value(
        employee_id,
        schedule_date,
        all_rates,
        "hourly_rate"
    )


def get_salary_for_date(employee_id, schedule_date, all_salaries):
    return get_effective_value(
        employee_id,
        schedule_date,
        all_salaries,
        "annual_salary"
    )


def get_current_employee_rate(employee_id):
    history = get_employee_rate_history(employee_id)
    return get_rate_for_date(employee_id, date.today(), history)


def get_current_employee_salary(employee_id):
    history = get_employee_salary_history(employee_id)
    return get_salary_for_date(employee_id, date.today(), history)


# ============================================================
# ADD TEAM MEMBER
# ============================================================

def add_team_member(
    employee_name,
    compensation_type,
    compensation_amount,
    effective_date
):

    employee_name = employee_name.strip()
    compensation_type = compensation_type.lower().strip()

    if not employee_name:
        raise ValueError("Team member name is required.")

    if compensation_type not in ("hourly", "salary"):
        raise ValueError("Compensation type must be Hourly or Salary.")

    if compensation_amount is None or float(compensation_amount) <= 0:
        raise ValueError("Compensation amount must be greater than $0.")

    existing = (
        supabase
        .table("labor_employees")
        .select("id, employee_name")
        .ilike("employee_name", employee_name)
        .execute()
    )

    if existing.data:
        raise ValueError(f"{employee_name} already exists.")

    employee_response = (
        supabase
        .table("labor_employees")
        .insert(
            {
                "employee_name": employee_name,
                "active": True,
                "compensation_type": compensation_type
            }
        )
        .execute()
    )

    if not employee_response.data:
        raise Exception("Employee could not be created.")

    employee_id = employee_response.data[0]["id"]

    if compensation_type == "hourly":
        table_name = "labor_pay_rates"
        value_name = "hourly_rate"
    else:
        table_name = "labor_salaries"
        value_name = "annual_salary"

    response = (
        supabase
        .table(table_name)
        .insert(
            {
                "employee_id": employee_id,
                value_name: float(compensation_amount),
                "effective_date": effective_date.isoformat()
            }
        )
        .execute()
    )

    if not response.data:
        raise Exception("Compensation record could not be created.")

    clear_labor_cache()
    return employee_id


# ============================================================
# PAY / SALARY CHANGES
# ============================================================

def add_new_pay_rate(employee_id, hourly_rate, effective_date):

    if employee_id is None:
        raise ValueError("No employee was selected.")

    if hourly_rate is None or float(hourly_rate) <= 0:
        raise ValueError("Hourly rate must be greater than $0.")

    response = (
        supabase
        .table("labor_pay_rates")
        .insert(
            {
                "employee_id": int(employee_id),
                "hourly_rate": float(hourly_rate),
                "effective_date": effective_date.isoformat()
            }
        )
        .execute()
    )

    if not response.data:
        raise Exception("Supabase did not return the new pay-rate record.")

    clear_labor_cache()
    return response.data[0]


def add_new_salary(employee_id, annual_salary, effective_date):

    if employee_id is None:
        raise ValueError("No employee was selected.")

    if annual_salary is None or float(annual_salary) <= 0:
        raise ValueError("Annual salary must be greater than $0.")

    response = (
        supabase
        .table("labor_salaries")
        .insert(
            {
                "employee_id": int(employee_id),
                "annual_salary": float(annual_salary),
                "effective_date": effective_date.isoformat()
            }
        )
        .execute()
    )

    if not response.data:
        raise Exception("Supabase did not return the new salary record.")

    (
        supabase
        .table("labor_employees")
        .update({"compensation_type": "salary"})
        .eq("id", int(employee_id))
        .execute()
    )

    clear_labor_cache()
    return response.data[0]


def set_employee_active(employee_id, active):

    (
        supabase
        .table("labor_employees")
        .update({"active": bool(active)})
        .eq("id", int(employee_id))
        .execute()
    )

    clear_labor_cache()


# ============================================================
# BUILD LABOR COST
# ============================================================

def build_labor_cost_data(
    employee_hours_df,
    employees_db,
    rates_db,
    salaries_db,
    aliases_db
):

    records = []

    for _, row in employee_hours_df.iterrows():

        schedule_name = row["employee"].strip()

        employee_data = find_employee_by_name(
            schedule_name,
            employees_db,
            aliases_db
        )

        if employee_data is None:
            records.append(
                {
                    "employee": schedule_name,
                    "date": row["date"],
                    "paid_hours": row["post_break_hours"],
                    "labor_cost": None,
                    "compensation_type": None,
                    "matched": False,
                    "has_rate": False
                }
            )
            continue

        compensation_type = employee_data.get("compensation_type") or "hourly"

        if compensation_type == "salary":
            annual_salary = get_salary_for_date(
                employee_data["id"],
                row["date"],
                salaries_db
            )

            records.append(
                {
                    "employee": schedule_name,
                    "date": row["date"],
                    "paid_hours": row["post_break_hours"],
                    "labor_cost": 0.0 if annual_salary is not None else None,
                    "compensation_type": "salary",
                    "matched": True,
                    "has_rate": annual_salary is not None
                }
            )
            continue

        hourly_rate = get_rate_for_date(
            employee_data["id"],
            row["date"],
            rates_db
        )

        if hourly_rate is None:
            records.append(
                {
                    "employee": schedule_name,
                    "date": row["date"],
                    "paid_hours": row["post_break_hours"],
                    "labor_cost": None,
                    "compensation_type": "hourly",
                    "matched": True,
                    "has_rate": False
                }
            )
            continue

        labor_cost = row["post_break_hours"] * hourly_rate

        records.append(
            {
                "employee": schedule_name,
                "date": row["date"],
                "paid_hours": row["post_break_hours"],
                "labor_cost": round(labor_cost, 2),
                "compensation_type": "hourly",
                "matched": True,
                "has_rate": True
            }
        )

    return pd.DataFrame(records)


def build_daily_salary_costs(dates, employees_db, salaries_db):
    """Allocate each active salaried employee's annual salary evenly across 52 weeks and 7 days."""

    records = []

    salaried_employees = [
        employee
        for employee in employees_db
        if employee.get("active", True)
        and (employee.get("compensation_type") or "hourly") == "salary"
    ]

    for schedule_date in dates:
        daily_salary_cost = 0.0
        missing_salary_names = []

        for employee in salaried_employees:
            annual_salary = get_salary_for_date(
                employee["id"],
                schedule_date,
                salaries_db
            )

            if annual_salary is None:
                missing_salary_names.append(employee["employee_name"])
                continue

            daily_salary_cost += annual_salary / 52 / 7

        records.append(
            {
                "date": schedule_date,
                "salary_labor_cost": round(daily_salary_cost, 2),
                "missing_salary_names": missing_salary_names
            }
        )

    return pd.DataFrame(records)


# ============================================================
# ACTUAL LABOR / PAY PERIOD REPORT
# ============================================================

def hours_text_to_decimal(value):
    value = clean_text(value)

    if not value or value == "---":
        return 0.0

    try:
        if ":" in value:
            hours, minutes = value.split(":", 1)
            return round(float(hours) + float(minutes) / 60, 4)

        return float(value)
    except Exception:
        return 0.0


def money_text_to_float(value):
    value = clean_text(value)

    if not value or value == "---":
        return 0.0

    try:
        return float(
            value.replace("$", "").replace(",", "").strip()
        )
    except Exception:
        return 0.0


@st.cache_data
def parse_pay_period_report(file_bytes):
    try:
        decoded = file_bytes.decode("utf-8-sig", errors="replace")
        rows = list(csv.reader(io.StringIO(decoded)))
    except Exception as e:
        raise Exception(f"Could not read pay period CSV: {e}")

    header_index = None

    for i, row in enumerate(rows):
        upper = [clean_text(value).upper() for value in row]

        if (
            len(upper) >= 16
            and "EMPLOYEE" in upper
            and "PERIOD" in upper
            and "REG" in upper
            and "OT" in upper
        ):
            header_index = i
            break

    if header_index is None:
        raise Exception(
            "This does not look like the expected Pay Period Report CSV."
        )

    records = []
    current_employee = None
    current_geid = None

    for row in rows[header_index + 1:]:
        row = list(row) + [""] * max(0, 16 - len(row))

        employee_name = clean_text(row[1])

        if employee_name:
            current_employee = employee_name
            current_geid = clean_text(row[2])

        period_value = clean_text(row[4])

        if (
            not current_employee
            or not period_value
            or period_value in ("Week 1 Total", "Grand Total")
        ):
            continue

        try:
            work_date = pd.to_datetime(
                period_value,
                format="%m/%d/%Y",
                errors="raise"
            ).date()
        except Exception:
            continue

        reg_hours = hours_text_to_decimal(row[5])
        ot_hours = hours_text_to_decimal(row[6])
        dbl_hours = hours_text_to_decimal(row[7])
        total_hours = hours_text_to_decimal(row[8])

        misc_code = clean_text(row[10]).upper()
        misc_hours = hours_text_to_decimal(row[11])
        misc_dollars = money_text_to_float(row[12])

        compliance_code = clean_text(row[13]).upper()
        compliance_hours = hours_text_to_decimal(row[14])
        compliance_dollars = money_text_to_float(row[15])

        pspt_dollars = (
            compliance_dollars
            if compliance_code == "PSPT"
            else 0.0
        )

        records.append({
            "employee": current_employee,
            "geid": current_geid,
            "date": work_date,
            "reg_hours": reg_hours,
            "ot_hours": ot_hours,
            "dbl_hours": dbl_hours,
            "hours_worked": total_hours,
            "misc_code": misc_code,
            "misc_hours": misc_hours,
            "misc_dollars": misc_dollars,
            "compliance_code": compliance_code,
            "compliance_hours": compliance_hours,
            "compliance_dollars": compliance_dollars,
            "pspt_dollars": pspt_dollars
        })

    if not records:
        return pd.DataFrame()

    return pd.DataFrame(records)


def build_actual_labor_data(
    actual_hours_df,
    employees_db,
    rates_db,
    salaries_db,
    aliases_db
):

    records = []

    for _, row in actual_hours_df.iterrows():
        employee_name = clean_text(row["employee"])

        employee_data = find_employee_by_name(
            employee_name,
            employees_db,
            aliases_db
        )

        base_record = {
            "employee": employee_name,
            "date": row["date"],
            "reg_hours": float(row["reg_hours"]),
            "ot_hours": float(row["ot_hours"]),
            "dbl_hours": float(row["dbl_hours"]),
            "hours_worked": float(row["hours_worked"]),
            "pspt_dollars": float(row["pspt_dollars"]),
            "other_pay_dollars": (
                float(row["misc_dollars"])
                + (
                    float(row["compliance_dollars"])
                    if clean_text(row["compliance_code"]).upper() != "PSPT"
                    else 0.0
                )
            )
        }

        if employee_data is None:
            records.append({
                **base_record,
                "compensation_type": None,
                "base_rate": None,
                "base_pay": None,
                "ot_pay": None,
                "dbl_pay": None,
                "salary_labor": 0.0,
                "actual_labor_cost": None,
                "matched": False,
                "has_rate": False
            })
            continue

        compensation_type = (
            employee_data.get("compensation_type") or "hourly"
        )

        if compensation_type == "salary":
            annual_salary = get_salary_for_date(
                employee_data["id"],
                row["date"],
                salaries_db
            )

            # Salary employees' punched hours are informational only.
            # Their fixed salary labor is added separately at the daily/weekly level
            # as total applicable annual salaries / 52 / 7.
            # Do not attach salary dollars to an individual payroll row.
            extra_pay = (
                base_record["pspt_dollars"]
                + base_record["other_pay_dollars"]
            )

            records.append({
                **base_record,
                "compensation_type": "salary",
                "base_rate": None,
                "base_pay": 0.0,
                "ot_pay": 0.0,
                "dbl_pay": 0.0,
                "salary_labor": 0.0,
                "actual_labor_cost": (
                    round(extra_pay, 2)
                    if annual_salary is not None
                    else None
                ),
                "matched": True,
                "has_rate": annual_salary is not None
            })
            continue

        hourly_rate = get_rate_for_date(
            employee_data["id"],
            row["date"],
            rates_db
        )

        if hourly_rate is None:
            records.append({
                **base_record,
                "compensation_type": "hourly",
                "base_rate": None,
                "base_pay": None,
                "ot_pay": None,
                "dbl_pay": None,
                "salary_labor": 0.0,
                "actual_labor_cost": None,
                "matched": True,
                "has_rate": False
            })
            continue

        base_pay = base_record["reg_hours"] * hourly_rate
        ot_pay = base_record["ot_hours"] * hourly_rate * 1.5
        # "DBL" is informational only. These hours are already included
        # in the employee's worked/regular hours, so DBL adds no extra pay.
        dbl_pay = 0.0

        total = (
            base_pay
            + ot_pay
            + dbl_pay
            + base_record["pspt_dollars"]
            + base_record["other_pay_dollars"]
        )

        records.append({
            **base_record,
            "compensation_type": "hourly",
            "base_rate": float(hourly_rate),
            "base_pay": round(base_pay, 2),
            "ot_pay": round(ot_pay, 2),
            "dbl_pay": round(dbl_pay, 2),
            "salary_labor": 0.0,
            "actual_labor_cost": round(total, 2),
            "matched": True,
            "has_rate": True
        })

    return pd.DataFrame(records)



# ============================================================
# SAVED ACTUAL LABOR
# ============================================================

def save_final_actual_labor(daily_actual_df, actual_labor_df):
    if daily_actual_df.empty:
        raise ValueError("There is no actual labor data to save.")

    week_start = min(daily_actual_df["date"])
    week_end = max(daily_actual_df["date"])
    total_sales = float(daily_actual_df["sales"].sum())
    total_labor_cost = float(daily_actual_df["labor_cost"].sum())
    actual_labor_pct = (
        total_labor_cost / total_sales * 100
        if total_sales > 0 else None
    )

    week_payload = {
        "week_start": week_start.isoformat(),
        "week_end": week_end.isoformat(),
        "total_sales": total_sales,
        "total_labor_cost": total_labor_cost,
        "actual_labor_pct": actual_labor_pct,
        "total_hours": float(daily_actual_df["hours_worked"].sum()),
        "total_pspt": float(daily_actual_df["pspt"].sum()),
        "saved_at": datetime.now().isoformat()
    }

    existing = (
        supabase.table("labor_actual_weeks")
        .select("id")
        .eq("week_start", week_start.isoformat())
        .execute()
    )

    if existing.data:
        week_id = existing.data[0]["id"]
        supabase.table("labor_actual_weeks").update(
            week_payload
        ).eq("id", int(week_id)).execute()
        supabase.table("labor_actual_days").delete().eq(
            "actual_week_id", int(week_id)
        ).execute()
        supabase.table("labor_actual_employee_days").delete().eq(
            "actual_week_id", int(week_id)
        ).execute()
    else:
        response = (
            supabase.table("labor_actual_weeks")
            .insert(week_payload)
            .execute()
        )
        if not response.data:
            raise Exception("The weekly actual labor record could not be saved.")
        week_id = response.data[0]["id"]

    day_records = []
    for _, row in daily_actual_df.iterrows():
        day_records.append({
            "actual_week_id": int(week_id),
            "work_date": parse_date_value(row["date"]).isoformat(),
            "actual_sales": float(row["sales"]),
            "actual_labor_cost": float(row["labor_cost"]),
            "actual_labor_pct": (
                float(row["labor_pct"])
                if pd.notna(row["labor_pct"]) else None
            ),
            "actual_hours": float(row["hours_worked"]),
            "regular_pay": float(row["regular_pay"]),
            "overtime_pay": float(row["ot_pay"]),
            "salary_labor": float(row["salary_labor"]),
            "pspt_pay": float(row["pspt"]),
            "other_pay": float(row["other_pay"]),
            "employee_count": (
                len(row["workers"])
                if isinstance(row["workers"], (list, tuple, set))
                else int(row["workers"])
            )
        })

    if day_records:
        response = (
            supabase.table("labor_actual_days")
            .insert(day_records)
            .execute()
        )
        if not response.data:
            raise Exception("The daily actual labor details could not be saved.")

    employee_records = []
    for _, row in actual_labor_df.iterrows():
        if pd.isna(row["actual_labor_cost"]):
            continue

        employee_data = find_employee_by_name(
            row["employee"], employees_db, aliases_db
        )

        employee_records.append({
            "actual_week_id": int(week_id),
            "employee_id": (
                int(employee_data["id"])
                if employee_data is not None else None
            ),
            "employee_name": clean_text(row["employee"]),
            "work_date": parse_date_value(row["date"]).isoformat(),
            "regular_hours": float(row["reg_hours"]),
            "overtime_hours": float(row["ot_hours"]),
            "worked_hours": float(row["hours_worked"]),
            "base_rate": (
                float(row["base_rate"])
                if pd.notna(row["base_rate"]) else None
            ),
            "regular_pay": float(row["base_pay"] or 0),
            "overtime_pay": float(row["ot_pay"] or 0),
            "salary_labor": float(row["salary_labor"] or 0),
            "pspt_pay": float(row["pspt_dollars"] or 0),
            "other_pay": float(row["other_pay_dollars"] or 0),
            "actual_labor_cost": float(row["actual_labor_cost"])
        })

    if employee_records:
        response = (
            supabase.table("labor_actual_employee_days")
            .insert(employee_records)
            .execute()
        )
        if not response.data:
            raise Exception("Employee actual labor details could not be saved.")

    clear_actual_labor_cache()
    return week_id


@st.cache_data(ttl=60)
def get_saved_actual_weeks():
    response = (
        supabase.table("labor_actual_weeks")
        .select(
            "id, week_start, week_end, total_sales, total_labor_cost, "
            "actual_labor_pct, total_hours, total_pspt, saved_at"
        )
        .order("week_start", desc=True)
        .execute()
    )
    return response.data or []


@st.cache_data(ttl=60)
def get_saved_actual_days(actual_week_id):
    response = (
        supabase.table("labor_actual_days")
        .select(
            "id, actual_week_id, work_date, actual_sales, actual_labor_cost, "
            "actual_labor_pct, actual_hours, regular_pay, overtime_pay, "
            "salary_labor, pspt_pay, other_pay, employee_count"
        )
        .eq("actual_week_id", int(actual_week_id))
        .order("work_date")
        .execute()
    )
    return response.data or []


@st.cache_data(ttl=60)
def get_all_saved_actual_days():
    response = (
        supabase.table("labor_actual_days")
        .select(
            "id, actual_week_id, work_date, actual_sales, actual_labor_cost, "
            "actual_labor_pct, actual_hours, regular_pay, overtime_pay, "
            "salary_labor, pspt_pay, other_pay, employee_count"
        )
        .order("work_date")
        .execute()
    )
    return response.data or []


def clear_actual_labor_cache():
    get_saved_actual_weeks.clear()
    get_saved_actual_days.clear()
    get_all_saved_actual_days.clear()


# ============================================================
# SAVED LABOR PROJECTIONS
# ============================================================

def save_final_projection(daily_plan_df, schedule_df, target_labor_pct):
    if daily_plan_df.empty:
        raise ValueError("There is no labor plan to save.")

    week_start = min(daily_plan_df["date"])
    week_end = max(daily_plan_df["date"])

    existing = (
        supabase.table("labor_projection_weeks")
        .select("id")
        .eq("week_start", week_start.isoformat())
        .execute()
    )

    week_payload = {
        "week_start": week_start.isoformat(),
        "week_end": week_end.isoformat(),
        "target_labor_pct": float(target_labor_pct),
        "saved_at": datetime.now().isoformat()
    }

    if existing.data:
        week_id = existing.data[0]["id"]
        (
            supabase.table("labor_projection_weeks")
            .update(week_payload)
            .eq("id", int(week_id))
            .execute()
        )
        (
            supabase.table("labor_projection_days")
            .delete()
            .eq("projection_week_id", int(week_id))
            .execute()
        )
    else:
        response = (
            supabase.table("labor_projection_weeks")
            .insert(week_payload)
            .execute()
        )
        if not response.data:
            raise Exception("The weekly projection could not be saved.")
        week_id = response.data[0]["id"]

    day_records = []

    for _, row in daily_plan_df.iterrows():
        schedule_date = parse_date_value(row["date"])

        workers = sorted(
            schedule_df[schedule_df["date"] == schedule_date]["employee"]
            .dropna()
            .astype(str)
            .unique()
            .tolist()
        )

        projected_sales = float(row["sales"])
        sales_needed = float(row["sales_needed"])
        labor_pct = (
            float(row["labor_pct"])
            if pd.notna(row["labor_pct"])
            else None
        )

        sales_needed_pct_of_projection = (
            sales_needed / projected_sales * 100
            if projected_sales > 0
            else None
        )

        additional_sales_needed = max(
            sales_needed - projected_sales,
            0
        )

        additional_sales_pct = (
            additional_sales_needed / projected_sales * 100
            if projected_sales > 0
            else None
        )

        day_records.append({
            "projection_week_id": int(week_id),
            "schedule_date": schedule_date.isoformat(),
            "projected_sales": projected_sales,
            "anticipated_labor_pct": labor_pct,
            "target_labor_pct": float(target_labor_pct),
            "sales_needed_at_target": sales_needed,
            "sales_needed_pct_of_projection": sales_needed_pct_of_projection,
            "additional_sales_needed": additional_sales_needed,
            "additional_sales_pct": additional_sales_pct,
            "projected_labor_cost": float(row["labor_cost"]),
            "hourly_labor_cost": float(row["hourly_labor_cost"]),
            "salary_labor_cost": float(row["salary_labor_cost"]),
            "projected_paid_hours": float(row["paid_hours"]),
            "scheduled_employees": workers
        })

    response = (
        supabase.table("labor_projection_days")
        .insert(day_records)
        .execute()
    )

    if not response.data:
        raise Exception("The daily projection details could not be saved.")

    # Replace the employee-level projection snapshot for this week.
    # This freezes the originally projected employee hours/rate/labor so
    # future Actual-vs-Projected comparisons can explain labor changes.
    (
        supabase.table("labor_projection_employee_days")
        .delete()
        .eq("projection_week_id", int(week_id))
        .execute()
    )

    employee_hours_snapshot = build_employee_hours_table(schedule_df)

    if not employee_hours_snapshot.empty:
        employee_labor_snapshot = build_labor_cost_data(
            employee_hours_snapshot,
            employees_db,
            rates_db,
            salaries_db,
            aliases_db
        )

        employee_snapshot_records = []

        for _, employee_row in employee_labor_snapshot.iterrows():
            employee_data = find_employee_by_name(
                employee_row["employee"],
                employees_db,
                aliases_db
            )

            employee_id = (
                int(employee_data["id"])
                if employee_data is not None
                else None
            )

            compensation_type = (
                employee_row.get("compensation_type")
                if pd.notna(employee_row.get("compensation_type"))
                else None
            )

            projected_rate = None

            if (
                employee_data is not None
                and compensation_type == "hourly"
            ):
                projected_rate = get_rate_for_date(
                    employee_data["id"],
                    employee_row["date"],
                    rates_db
                )

            projected_labor_cost = employee_row.get("labor_cost")

            # Salary hours remain informational. Fixed salary labor is already
            # included in the daily projection and is not assigned to the
            # employee's worked hours.
            if pd.isna(projected_labor_cost):
                projected_labor_cost = 0.0

            employee_snapshot_records.append({
                "projection_week_id": int(week_id),
                "employee_id": employee_id,
                "employee_name": clean_text(employee_row["employee"]),
                "work_date": parse_date_value(
                    employee_row["date"]
                ).isoformat(),
                "compensation_type": compensation_type,
                "projected_paid_hours": float(
                    employee_row["paid_hours"]
                ),
                "projected_rate": (
                    float(projected_rate)
                    if projected_rate is not None
                    else None
                ),
                "projected_labor_cost": float(projected_labor_cost)
            })

        if employee_snapshot_records:
            snapshot_response = (
                supabase.table("labor_projection_employee_days")
                .insert(employee_snapshot_records)
                .execute()
            )

            if not snapshot_response.data:
                raise Exception(
                    "The employee-level projection snapshot could not be saved."
                )

    clear_projection_cache()
    clear_projection_employee_cache()
    return week_id


@st.cache_data(ttl=60)
def get_saved_projection_weeks():
    response = (
        supabase.table("labor_projection_weeks")
        .select("id, week_start, week_end, target_labor_pct, saved_at")
        .order("week_start", desc=True)
        .execute()
    )
    return response.data or []


@st.cache_data(ttl=60)
def get_saved_projection_days(projection_week_id):
    response = (
        supabase.table("labor_projection_days")
        .select(
            "id, projection_week_id, schedule_date, projected_sales, "
            "anticipated_labor_pct, target_labor_pct, sales_needed_at_target, "
            "sales_needed_pct_of_projection, additional_sales_needed, "
            "additional_sales_pct, projected_labor_cost, hourly_labor_cost, "
            "salary_labor_cost, projected_paid_hours, scheduled_employees"
        )
        .eq("projection_week_id", int(projection_week_id))
        .order("schedule_date")
        .execute()
    )
    return response.data or []


@st.cache_data(ttl=60)
def get_all_saved_projection_days():
    response = (
        supabase.table("labor_projection_days")
        .select(
            "id, projection_week_id, schedule_date, projected_sales, "
            "anticipated_labor_pct, target_labor_pct, sales_needed_at_target, "
            "sales_needed_pct_of_projection, additional_sales_needed, "
            "additional_sales_pct, projected_labor_cost, hourly_labor_cost, "
            "salary_labor_cost, projected_paid_hours, scheduled_employees"
        )
        .order("schedule_date")
        .execute()
    )
    return response.data or []


def clear_projection_cache():
    get_saved_projection_weeks.clear()
    get_saved_projection_days.clear()
    get_all_saved_projection_days.clear()


@st.cache_data(ttl=60)
def get_saved_projection_employee_days(projection_week_id):
    response = (
        supabase.table("labor_projection_employee_days")
        .select(
            "id, projection_week_id, employee_id, employee_name, work_date, "
            "compensation_type, projected_paid_hours, projected_rate, "
            "projected_labor_cost"
        )
        .eq("projection_week_id", int(projection_week_id))
        .order("work_date")
        .order("employee_name")
        .execute()
    )
    return response.data or []


def clear_projection_employee_cache():
    get_saved_projection_employee_days.clear()


# ============================================================
# PROJECTED VS ACTUAL COMPARISON HELPERS
# ============================================================

def build_week_comparison(projection_days, actual_days):
    """Build weekly and daily projected-vs-actual comparison data."""

    projected_df = pd.DataFrame(projection_days or [])
    actual_df = pd.DataFrame(actual_days or [])

    if projected_df.empty or actual_df.empty:
        return None, pd.DataFrame()

    projected_df["date"] = projected_df["schedule_date"].apply(parse_date_value)
    actual_df["date"] = actual_df["work_date"].apply(parse_date_value)

    projected_daily = (
        projected_df.groupby("date", as_index=False)
        .agg(
            projected_sales=("projected_sales", "sum"),
            projected_labor=("projected_labor_cost", "sum"),
            projected_hours=("projected_paid_hours", "sum")
        )
    )

    actual_daily = (
        actual_df.groupby("date", as_index=False)
        .agg(
            actual_sales=("actual_sales", "sum"),
            actual_labor=("actual_labor_cost", "sum"),
            actual_hours=("actual_hours", "sum")
        )
    )

    daily = pd.merge(
        projected_daily,
        actual_daily,
        on="date",
        how="outer"
    ).sort_values("date")

    numeric_columns = [
        "projected_sales",
        "projected_labor",
        "projected_hours",
        "actual_sales",
        "actual_labor",
        "actual_hours"
    ]

    for column in numeric_columns:
        daily[column] = pd.to_numeric(
            daily[column],
            errors="coerce"
        ).fillna(0.0)

    daily["sales_difference"] = (
        daily["actual_sales"] - daily["projected_sales"]
    )

    daily["labor_difference"] = (
        daily["actual_labor"] - daily["projected_labor"]
    )

    daily["hours_difference"] = (
        daily["actual_hours"] - daily["projected_hours"]
    )

    daily["projected_labor_pct"] = daily.apply(
        lambda row: (
            row["projected_labor"] / row["projected_sales"] * 100
            if row["projected_sales"] > 0
            else None
        ),
        axis=1
    )

    daily["actual_labor_pct"] = daily.apply(
        lambda row: (
            row["actual_labor"] / row["actual_sales"] * 100
            if row["actual_sales"] > 0
            else None
        ),
        axis=1
    )

    daily["labor_pct_difference"] = daily.apply(
        lambda row: (
            row["actual_labor_pct"] - row["projected_labor_pct"]
            if pd.notna(row["actual_labor_pct"])
            and pd.notna(row["projected_labor_pct"])
            else None
        ),
        axis=1
    )

    projected_sales = float(daily["projected_sales"].sum())
    projected_labor = float(daily["projected_labor"].sum())
    projected_hours = float(daily["projected_hours"].sum())

    actual_sales = float(daily["actual_sales"].sum())
    actual_labor = float(daily["actual_labor"].sum())
    actual_hours = float(daily["actual_hours"].sum())

    projected_labor_pct = (
        projected_labor / projected_sales * 100
        if projected_sales > 0
        else None
    )

    actual_labor_pct = (
        actual_labor / actual_sales * 100
        if actual_sales > 0
        else None
    )

    weekly = {
        "projected_sales": projected_sales,
        "actual_sales": actual_sales,
        "sales_difference": actual_sales - projected_sales,
        "projected_labor": projected_labor,
        "actual_labor": actual_labor,
        "labor_difference": actual_labor - projected_labor,
        "projected_hours": projected_hours,
        "actual_hours": actual_hours,
        "hours_difference": actual_hours - projected_hours,
        "projected_labor_pct": projected_labor_pct,
        "actual_labor_pct": actual_labor_pct,
        "labor_pct_difference": (
            actual_labor_pct - projected_labor_pct
            if actual_labor_pct is not None
            and projected_labor_pct is not None
            else None
        )
    }

    return weekly, daily


def build_employee_comparison(projection_employee_days, actual_week_id):
    """Compare projected employee hours/cost with saved actual employee labor."""

    projected_df = pd.DataFrame(projection_employee_days or [])

    actual_response = (
        supabase.table("labor_actual_employee_days")
        .select(
            "employee_id, employee_name, work_date, regular_hours, "
            "overtime_hours, worked_hours, base_rate, regular_pay, "
            "overtime_pay, salary_labor, pspt_pay, other_pay, "
            "actual_labor_cost"
        )
        .eq("actual_week_id", int(actual_week_id))
        .execute()
    )

    actual_df = pd.DataFrame(actual_response.data or [])

    if projected_df.empty and actual_df.empty:
        return pd.DataFrame()

    if not projected_df.empty:
        projected_df["employee_key"] = projected_df.apply(
            lambda row: (
                f"id:{int(row['employee_id'])}"
                if pd.notna(row.get("employee_id"))
                else f"name:{normalize_name(row['employee_name'])}"
            ),
            axis=1
        )

        projected_grouped = (
            projected_df.groupby("employee_key", as_index=False)
            .agg(
                employee_name=("employee_name", "first"),
                compensation_type=("compensation_type", "first"),
                projected_hours=("projected_paid_hours", "sum"),
                projected_labor=("projected_labor_cost", "sum"),
                projected_rate=("projected_rate", "max")
            )
        )
    else:
        projected_grouped = pd.DataFrame(
            columns=[
                "employee_key", "employee_name", "compensation_type",
                "projected_hours", "projected_labor", "projected_rate"
            ]
        )

    if not actual_df.empty:
        actual_df["employee_key"] = actual_df.apply(
            lambda row: (
                f"id:{int(row['employee_id'])}"
                if pd.notna(row.get("employee_id"))
                else f"name:{normalize_name(row['employee_name'])}"
            ),
            axis=1
        )

        numeric_actual_columns = [
            "regular_hours", "overtime_hours", "worked_hours",
            "regular_pay", "overtime_pay", "salary_labor",
            "pspt_pay", "other_pay", "actual_labor_cost"
        ]

        for column in numeric_actual_columns:
            actual_df[column] = pd.to_numeric(
                actual_df[column], errors="coerce"
            ).fillna(0.0)

        actual_grouped = (
            actual_df.groupby("employee_key", as_index=False)
            .agg(
                actual_employee_name=("employee_name", "first"),
                actual_hours=("worked_hours", "sum"),
                actual_regular_pay=("regular_pay", "sum"),
                actual_ot=("overtime_pay", "sum"),
                actual_pspt=("pspt_pay", "sum"),
                actual_other_pay=("other_pay", "sum"),
                actual_labor=("actual_labor_cost", "sum"),
                actual_rate=("base_rate", "max")
            )
        )
    else:
        actual_grouped = pd.DataFrame(
            columns=[
                "employee_key", "actual_employee_name", "actual_hours",
                "actual_regular_pay", "actual_ot", "actual_pspt",
                "actual_other_pay", "actual_labor", "actual_rate"
            ]
        )

    comparison = pd.merge(
        projected_grouped,
        actual_grouped,
        on="employee_key",
        how="outer"
    )

    comparison["employee"] = comparison.apply(
        lambda row: (
            row["employee_name"]
            if pd.notna(row.get("employee_name"))
            else row.get("actual_employee_name", "")
        ),
        axis=1
    )

    for column in [
        "projected_hours", "projected_labor", "actual_hours",
        "actual_regular_pay", "actual_ot", "actual_pspt",
        "actual_other_pay", "actual_labor"
    ]:
        comparison[column] = pd.to_numeric(
            comparison.get(column, 0),
            errors="coerce"
        ).fillna(0.0)

    comparison["hours_difference"] = (
        comparison["actual_hours"] - comparison["projected_hours"]
    )

    comparison["labor_difference"] = (
        comparison["actual_labor"] - comparison["projected_labor"]
    )

    comparison["premium_total"] = (
        comparison["actual_ot"]
        + comparison["actual_pspt"]
        + comparison["actual_other_pay"]
    )

    comparison["schedule_change"] = comparison.apply(
        lambda row: (
            "Added"
            if row["projected_hours"] == 0 and row["actual_hours"] > 0
            else "Removed"
            if row["projected_hours"] > 0 and row["actual_hours"] == 0
            else "Hours changed"
            if abs(row["hours_difference"]) > 0.01
            else "No hours change"
        ),
        axis=1
    )

    return comparison.sort_values(
        ["labor_difference", "hours_difference"],
        ascending=[False, False]
    )


def format_signed_currency(value):
    value = float(value or 0)
    sign = "+" if value > 0 else ""
    return f"{sign}${value:,.2f}"


def format_signed_number(value, decimals=2):
    value = float(value or 0)
    sign = "+" if value > 0 else ""
    return f"{sign}{value:,.{decimals}f}"


def format_pct(value):
    if value is None or pd.isna(value):
        return "—"
    return f"{float(value):.2f}%"


def format_signed_pct(value):
    if value is None or pd.isna(value):
        return "—"
    value = float(value)
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.2f} pts"


# ============================================================
# SCHEDULE CHECKS
# ============================================================

def run_schedule_checks(
    schedule_df
):

    issues = []

    employee_hours = (
        build_employee_hours_table(
            schedule_df
        )
    )

    for _, row in (
        employee_hours.iterrows()
    ):

        if (
            row[
                "pre_break_hours"
            ]
            > 12
        ):

            issues.append(
                {
                    "date":
                        row[
                            "date"
                        ],

                    "employee":
                        row[
                            "employee"
                        ],

                    "type":
                        "Long Day",

                    "severity":
                        "Warning",

                    "details":
                        (
                            f"Scheduled for "
                            f"{row['pre_break_hours']:.2f} "
                            f"hours before meal periods."
                        )
                }
            )

    released = (
        schedule_df[
            schedule_df[
                "shift_released"
            ]
            == True
        ]
    )

    for _, row in (
        released.iterrows()
    ):

        issues.append(
            {
                "date":
                    row[
                        "date"
                    ],

                "employee":
                    row[
                        "employee"
                    ],

                "type":
                    "Shift Released",

                "severity":
                    "Info",

                "details":
                    (
                        f"{row['job_type']} "
                        f"{format_time(row['start_time'])}"
                        f" - "
                        f"{format_time(row['end_time'])}"
                    )
            }
        )

    return pd.DataFrame(
        issues
    )


# ============================================================
# LOAD SUPABASE DATA
# ============================================================

try:

    employees_db, rates_db, salaries_db, aliases_db = (
        get_labor_database()
    )

    supabase_error = None

except Exception as e:

    employees_db = []
    rates_db = []
    salaries_db = []
    aliases_db = []
    supabase_error = e


# ============================================================
# MAIN TABS
# ============================================================

(
    tab1,
    tab2,
    tab3,
    tab4,
    tab5,
    tab6,
    tab7,
    tab8,
    tab9
) = st.tabs(
    [
        "📊 Overview",
        "👥 Staffing",
        "⏱ Employee Hours",
        "💰 Labor Planner",
        "👤 Team Management",
        "⚠️ Schedule Checks",
        "📁 Saved Projections",
        "🧾 Actual Labor",
        "🔄 Comparisons"
    ]
)


# ============================================================
# TAB 5 — TEAM MANAGEMENT
# ============================================================

with tab5:
    st.header("👤 Team Management")
    st.caption(
        "Add hourly or salaried team members, record compensation changes, "
        "and manage employee status."
    )

    if supabase_error:
        st.error("Could not connect to the labor tables in Supabase.")
        st.exception(supabase_error)

    else:
        add_tab, pay_tab, salary_tab, alias_tab, team_tab = st.tabs(
            [
                "➕ Add Team Member",
                "💵 Hourly Pay Changes",
                "💼 Salary Management",
                "🏷️ Employee Aliases",
                "👥 View Team"
            ]
        )

        with add_tab:
            st.subheader("Add Team Member")
            st.caption("Enter the employee's name exactly as it appears on the schedule.")

            compensation_type_label = st.radio(
                "Compensation Type",
                ["Hourly", "Salary"],
                horizontal=True,
                key="new_compensation_type"
            )

            with st.form("add_employee_form"):
                new_name = st.text_input(
                    "Team Member Name",
                    placeholder="Example: Alexandria DeBetta"
                )

                if compensation_type_label == "Hourly":
                    compensation_amount = st.number_input(
                        "Hourly Rate",
                        min_value=0.00,
                        value=0.00,
                        step=0.25,
                        format="%.2f"
                    )
                    effective_label = "Rate Effective Date"
                else:
                    compensation_amount = st.number_input(
                        "Annual Salary",
                        min_value=0.00,
                        value=0.00,
                        step=1000.00,
                        format="%.2f"
                    )
                    effective_label = "Salary Effective Date"

                new_effective_date = st.date_input(
                    effective_label,
                    value=date.today()
                )

                add_button = st.form_submit_button(
                    "Add Team Member",
                    type="primary"
                )

            if add_button:
                try:
                    add_team_member(
                        new_name,
                        compensation_type_label.lower(),
                        compensation_amount,
                        new_effective_date
                    )
                    st.success(f"{new_name.strip()} was added successfully.")
                    employees_db, rates_db, salaries_db, aliases_db = (
                        get_labor_database()
                    )
                except Exception as e:
                    st.error(str(e))

        with pay_tab:
            st.subheader("Hourly Pay Change / Promotion")
            st.caption(
                "Use this for hourly employees. Previous hourly rates are preserved."
            )

            hourly_employees = [
                employee
                for employee in employees_db
                if (employee.get("compensation_type") or "hourly") == "hourly"
            ]

            if not hourly_employees:
                st.info("No hourly team members are currently set up.")
            else:
                employee_options = {
                    employee["employee_name"]: employee["id"]
                    for employee in hourly_employees
                }

                selected_team_member = st.selectbox(
                    "Team Member",
                    list(employee_options.keys()),
                    key="promotion_employee"
                )
                selected_employee_id = employee_options[selected_team_member]
                today_rate = get_current_employee_rate(selected_employee_id)

                if today_rate is not None:
                    st.metric("Current Rate", f"${today_rate:,.2f}/hr")
                else:
                    st.warning("This employee does not currently have an effective hourly rate.")

                with st.form("promotion_form"):
                    promotion_rate = st.number_input(
                        "New Hourly Rate",
                        min_value=0.00,
                        value=0.00,
                        step=0.25,
                        format="%.2f"
                    )
                    promotion_date = st.date_input(
                        "Effective Date",
                        value=date.today(),
                        key="promotion_date"
                    )
                    promotion_button = st.form_submit_button(
                        "Save New Pay Rate",
                        type="primary"
                    )

                if promotion_button:
                    try:
                        saved_rate = add_new_pay_rate(
                            selected_employee_id,
                            promotion_rate,
                            promotion_date
                        )
                        st.success(
                            f"New pay rate saved for {selected_team_member}: "
                            f"${float(saved_rate['hourly_rate']):,.2f}/hr "
                            f"effective {saved_rate['effective_date']}."
                        )
                        employees_db, rates_db, salaries_db, aliases_db = (
                        get_labor_database()
                    )
                    except Exception as e:
                        st.error(f"Pay rate was NOT saved: {e}")

                try:
                    rate_history = get_employee_rate_history(selected_employee_id)
                except Exception as e:
                    rate_history = []
                    st.error(f"Could not load pay history: {e}")

                if rate_history:
                    st.divider()
                    st.subheader("Hourly Pay History")
                    history_df = pd.DataFrame(rate_history)
                    history_df["hourly_rate"] = history_df["hourly_rate"].apply(
                        lambda x: f"${float(x):,.2f}"
                    )
                    history_df = history_df[["effective_date", "hourly_rate"]]
                    history_df.columns = ["Effective Date", "Hourly Rate"]
                    st.dataframe(history_df, use_container_width=True, hide_index=True)

        with salary_tab:
            st.subheader("Salary Management")
            st.caption(
                "Designate an existing team member as salaried or record a salary change. "
                "Salary labor is annual salary ÷ 52 for the week."
            )

            if not employees_db:
                st.info("Add a team member first.")
            else:
                salary_options = {
                    employee["employee_name"]: employee["id"]
                    for employee in employees_db
                }

                salary_name = st.selectbox(
                    "Team Member",
                    list(salary_options.keys()),
                    key="salary_employee"
                )
                salary_employee_id = salary_options[salary_name]
                selected_salary_employee = next(
                    employee
                    for employee in employees_db
                    if int(employee["id"]) == int(salary_employee_id)
                )
                current_type = selected_salary_employee.get("compensation_type") or "hourly"
                current_salary = get_current_employee_salary(salary_employee_id)

                st.write(f"Compensation Type: **{current_type.title()}**")

                if current_salary is not None:
                    c1, c2 = st.columns(2)
                    c1.metric("Current Annual Salary", f"${current_salary:,.2f}")
                    c2.metric("Weekly Salary Labor", f"${current_salary / 52:,.2f}")
                elif current_type == "salary":
                    st.warning("This employee is marked salaried but has no effective salary record.")

                with st.form("salary_change_form"):
                    annual_salary = st.number_input(
                        "Annual Salary",
                        min_value=0.00,
                        value=0.00,
                        step=1000.00,
                        format="%.2f"
                    )
                    salary_effective_date = st.date_input(
                        "Effective Date",
                        value=date.today(),
                        key="salary_effective_date"
                    )
                    salary_button = st.form_submit_button(
                        "Save Salary / Mark as Salaried",
                        type="primary"
                    )

                if salary_button:
                    try:
                        saved_salary = add_new_salary(
                            salary_employee_id,
                            annual_salary,
                            salary_effective_date
                        )
                        st.success(
                            f"Salary saved for {salary_name}: "
                            f"${float(saved_salary['annual_salary']):,.2f}/year "
                            f"effective {saved_salary['effective_date']}."
                        )
                        employees_db, rates_db, salaries_db, aliases_db = (
                        get_labor_database()
                    )
                    except Exception as e:
                        st.error(f"Salary was NOT saved: {e}")

                try:
                    salary_history = get_employee_salary_history(salary_employee_id)
                except Exception as e:
                    salary_history = []
                    st.error(f"Could not load salary history: {e}")

                if salary_history:
                    st.divider()
                    st.subheader("Salary History")
                    salary_history_df = pd.DataFrame(salary_history)
                    salary_history_df["annual_salary"] = salary_history_df["annual_salary"].apply(
                        lambda x: f"${float(x):,.2f}"
                    )
                    salary_history_df = salary_history_df[["effective_date", "annual_salary"]]
                    salary_history_df.columns = ["Effective Date", "Annual Salary"]
                    st.dataframe(
                        salary_history_df,
                        use_container_width=True,
                        hide_index=True
                    )

        with alias_tab:
            st.subheader("Employee Aliases")
            st.caption(
                "Use an alias when a schedule or payroll report uses a different "
                "name for the same team member. Example: Hoffman Hoffman → Peter Hoffman."
            )

            if not employees_db:
                st.info("Add a team member before creating aliases.")
            else:
                alias_employee_options = {
                    employee["employee_name"]: employee["id"]
                    for employee in sorted(
                        employees_db,
                        key=lambda item: item["employee_name"].lower()
                    )
                }

                with st.form("add_employee_alias_form"):
                    alias_employee_name = st.selectbox(
                        "Team Member",
                        list(alias_employee_options.keys()),
                        key="alias_employee"
                    )
                    new_alias_name = st.text_input(
                        "Alias",
                        placeholder="Example: Hoffman Hoffman"
                    )
                    add_alias_button = st.form_submit_button(
                        "Add Alias",
                        type="primary"
                    )

                if add_alias_button:
                    try:
                        canonical_match = find_employee_by_name(
                            new_alias_name,
                            employees_db,
                            aliases_db
                        )
                        if canonical_match is not None:
                            raise ValueError(
                                f"{new_alias_name.strip()} already matches "
                                f"{canonical_match['employee_name']}."
                            )

                        add_employee_alias(
                            alias_employee_options[alias_employee_name],
                            new_alias_name
                        )
                        st.success(
                            f"Alias saved: {new_alias_name.strip()} → "
                            f"{alias_employee_name}"
                        )
                        st.rerun()
                    except Exception as e:
                        st.error(f"Alias was NOT saved: {e}")

                st.divider()
                st.subheader("Current Aliases")

                if not aliases_db:
                    st.info("No employee aliases have been added yet.")
                else:
                    employee_names_by_id = {
                        int(employee["id"]): employee["employee_name"]
                        for employee in employees_db
                    }

                    alias_records = [
                        {
                            "Team Member": employee_names_by_id.get(
                                int(alias["employee_id"]),
                                "Unknown Employee"
                            ),
                            "Alias": alias["alias_name"]
                        }
                        for alias in aliases_db
                    ]

                    st.dataframe(
                        pd.DataFrame(alias_records).sort_values(
                            ["Team Member", "Alias"]
                        ),
                        use_container_width=True,
                        hide_index=True
                    )

                    alias_delete_options = {}
                    for alias in aliases_db:
                        employee_name = employee_names_by_id.get(
                            int(alias["employee_id"]),
                            "Unknown Employee"
                        )
                        label = f"{alias['alias_name']} → {employee_name}"
                        alias_delete_options[label] = alias["id"]

                    alias_to_delete = st.selectbox(
                        "Alias to Delete",
                        list(alias_delete_options.keys()),
                        key="delete_alias_select"
                    )

                    if st.button("Delete Alias", key="delete_alias_button"):
                        try:
                            delete_employee_alias(
                                alias_delete_options[alias_to_delete]
                            )
                            st.success("Alias deleted.")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Alias was NOT deleted: {e}")

        with team_tab:
            st.subheader("Team Members")

            if not employees_db:
                st.info("No team members have been added yet.")
            else:
                team_records = []

                for employee in employees_db:
                    compensation_type = employee.get("compensation_type") or "hourly"

                    if compensation_type == "salary":
                        current_salary = get_current_employee_salary(employee["id"])
                        compensation_display = (
                            f"${current_salary:,.2f}/yr"
                            if current_salary is not None
                            else "No Salary"
                        )
                    else:
                        current_rate = get_current_employee_rate(employee["id"])
                        compensation_display = (
                            f"${current_rate:,.2f}/hr"
                            if current_rate is not None
                            else "No Rate"
                        )

                    team_records.append(
                        {
                            "Team Member": employee["employee_name"],
                            "Type": compensation_type.title(),
                            "Current Compensation": compensation_display,
                            "Status": "Active" if employee["active"] else "Inactive"
                        }
                    )

                st.dataframe(
                    pd.DataFrame(team_records),
                    use_container_width=True,
                    hide_index=True
                )

                st.divider()
                st.subheader("Employee Status")

                status_name = st.selectbox(
                    "Select Team Member",
                    [employee["employee_name"] for employee in employees_db],
                    key="status_employee"
                )
                status_employee = next(
                    employee
                    for employee in employees_db
                    if employee["employee_name"] == status_name
                )
                currently_active = status_employee["active"]
                st.write(f"Current Status: **{'Active' if currently_active else 'Inactive'}**")

                if currently_active:
                    if st.button("Deactivate Team Member", key="deactivate_employee"):
                        try:
                            set_employee_active(status_employee["id"], False)
                            st.success(f"{status_name} was deactivated.")
                        except Exception as e:
                            st.error(str(e))
                else:
                    if st.button("Reactivate Team Member", key="reactivate_employee"):
                        try:
                            set_employee_active(status_employee["id"], True)
                            st.success(f"{status_name} was reactivated.")
                        except Exception as e:
                            st.error(str(e))


# ============================================================
# TAB 7 — SAVED PROJECTIONS
# ============================================================

with tab7:
    st.header("📁 Saved Projections")
    st.caption(
        "Open a finished labor projection at any time and review weekly or monthly projected labor."
    )

    if supabase_error:
        st.error("Could not connect to Supabase.")
    else:
        try:
            saved_weeks = get_saved_projection_weeks()
            all_saved_days = get_all_saved_projection_days()
        except Exception as e:
            saved_weeks = []
            all_saved_days = []
            st.error(
                "Saved projection tables could not be loaded. "
                "Run the projection-table SQL in Supabase first."
            )
            st.exception(e)

        if not saved_weeks:
            st.info("No finished projections have been saved yet.")
        else:
            week_options = {}
            for week in saved_weeks:
                week_start = parse_date_value(week["week_start"])
                week_end = parse_date_value(week["week_end"])
                label = (
                    f"{week_start.strftime('%b %d, %Y')} - "
                    f"{week_end.strftime('%b %d, %Y')}"
                )
                week_options[label] = week

            selected_saved_week_label = st.selectbox(
                "Saved Week",
                list(week_options.keys()),
                key="saved_projection_week"
            )
            selected_saved_week = week_options[selected_saved_week_label]

            try:
                saved_days = get_saved_projection_days(selected_saved_week["id"])
            except Exception as e:
                saved_days = []
                st.error(f"Could not load this saved projection: {e}")

            if saved_days:
                target_display = float(selected_saved_week["target_labor_pct"])
                weekly_sales = sum(float(row.get("projected_sales") or 0) for row in saved_days)
                weekly_labor = sum(float(row.get("projected_labor_cost") or 0) for row in saved_days)
                weekly_labor_pct = (weekly_labor / weekly_sales * 100) if weekly_sales > 0 else None

                st.subheader("Weekly Projection")
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Projected Weekly Sales", f"${weekly_sales:,.2f}")
                c2.metric("Projected Weekly Labor", f"${weekly_labor:,.2f}")
                c3.metric(
                    "Projected Weekly Labor %",
                    f"{weekly_labor_pct:.2f}%" if weekly_labor_pct is not None else "—"
                )
                c4.metric("Saved Target Labor %", f"{target_display:.2f}%")

                st.caption(f"Saved: {selected_saved_week.get('saved_at', '')}")

                if all_saved_days:
                    month_keys = sorted(
                        {(parse_date_value(row["schedule_date"]).year, parse_date_value(row["schedule_date"]).month)
                         for row in all_saved_days},
                        reverse=True
                    )
                    month_labels = {
                        date(year, month, 1).strftime("%B %Y"): (year, month)
                        for year, month in month_keys
                    }

                    selected_week_date = parse_date_value(selected_saved_week["week_start"])
                    default_month_label = selected_week_date.strftime("%B %Y")
                    month_label_list = list(month_labels.keys())
                    default_index = (
                        month_label_list.index(default_month_label)
                        if default_month_label in month_label_list else 0
                    )

                    st.divider()
                    st.subheader("Monthly Projection")
                    selected_month_label = st.selectbox(
                        "Month",
                        month_label_list,
                        index=default_index,
                        key="saved_projection_month"
                    )
                    selected_year, selected_month = month_labels[selected_month_label]

                    month_days = [
                        row for row in all_saved_days
                        if (
                            parse_date_value(row["schedule_date"]).year == selected_year
                            and parse_date_value(row["schedule_date"]).month == selected_month
                        )
                    ]

                    monthly_sales = sum(float(row.get("projected_sales") or 0) for row in month_days)
                    monthly_labor = sum(float(row.get("projected_labor_cost") or 0) for row in month_days)
                    monthly_labor_pct = (monthly_labor / monthly_sales * 100) if monthly_sales > 0 else None
                    saved_day_count = len({row["schedule_date"] for row in month_days})
                    projection_week_ids = {row["projection_week_id"] for row in month_days}

                    m1, m2, m3, m4 = st.columns(4)
                    m1.metric("Projected Monthly Sales", f"${monthly_sales:,.2f}")
                    m2.metric("Projected Monthly Labor", f"${monthly_labor:,.2f}")
                    m3.metric(
                        "Projected Monthly Labor %",
                        f"{monthly_labor_pct:.2f}%" if monthly_labor_pct is not None else "—"
                    )
                    m4.metric("Saved Projection Days", str(saved_day_count))
                    st.caption(
                        f"Calculated from {saved_day_count} saved day(s) across "
                        f"{len(projection_week_ids)} saved week(s) in {selected_month_label}. "
                        "Weeks that cross months are split by the actual date."
                    )

                st.divider()
                st.subheader("Daily Saved Projection")
                saved_display = []

                for row in saved_days:
                    employees_list = row.get("scheduled_employees") or []
                    employees_text = (
                        employees_list if isinstance(employees_list, str)
                        else ", ".join(employees_list)
                    )
                    anticipated = row.get("anticipated_labor_pct")
                    sales_needed_pct = row.get("sales_needed_pct_of_projection")
                    additional_pct = row.get("additional_sales_pct")

                    saved_display.append({
                        "Day": parse_date_value(row["schedule_date"]).strftime("%A %m/%d"),
                        "Projected Sales": f"${float(row['projected_sales']):,.2f}",
                        "Anticipated Labor %": (
                            f"{float(anticipated):.2f}%" if anticipated is not None else "—"
                        ),
                        f"Sales Needed @ {float(row['target_labor_pct']):.1f}%": (
                            f"${float(row['sales_needed_at_target']):,.2f}"
                        ),
                        "Needed Sales as % of Projection": (
                            f"{float(sales_needed_pct):.2f}%" if sales_needed_pct is not None else "—"
                        ),
                        "Additional Sales Needed": f"${float(row['additional_sales_needed']):,.2f}",
                        "Additional Sales %": (
                            f"{float(additional_pct):.2f}%" if additional_pct is not None else "—"
                        ),
                        "Projected Labor Cost": f"${float(row['projected_labor_cost']):,.2f}",
                        "Who Was Scheduled": employees_text
                    })

                st.dataframe(
                    pd.DataFrame(saved_display),
                    use_container_width=True,
                    hide_index=True
                )


# ============================================================
# TAB 8 — ACTUAL LABOR
# ============================================================

with tab8:
    st.header("🧾 Actual Labor")
    st.caption(
        "Upload the posted Pay Period Report CSV. The app matches each "
        "team member to their effective Supabase pay rate, calculates "
        "regular pay, OT pay, and PSPT premium pay. Salary hours are informational only; fixed salary labor is included every week regardless of hours worked. DBL is informational only and adds no additional labor cost."
    )

    actual_file = st.file_uploader(
        "Upload Pay Period Report",
        type=["csv"],
        key="actual_pay_period_report"
    )

    if actual_file is None:
        st.info(
            "Upload the weekly Pay Period Report CSV to calculate actual labor."
        )

    elif supabase_error:
        st.error(
            "The pay report was uploaded, but Supabase compensation data "
            "could not be loaded."
        )
        st.exception(supabase_error)

    else:
        try:
            actual_hours_df = parse_pay_period_report(
                actual_file.getvalue()
            )
        except Exception as e:
            actual_hours_df = pd.DataFrame()
            st.error(f"Could not read the Pay Period Report: {e}")

        if actual_hours_df.empty:
            st.warning(
                "No daily punched-hour records were found in this report."
            )
        else:
            actual_labor_df = build_actual_labor_data(
                actual_hours_df,
                employees_db,
                rates_db,
                salaries_db,
                aliases_db
            )

            unmatched_actual = sorted(
                actual_labor_df[
                    actual_labor_df["matched"] == False
                ]["employee"].unique()
            )

            missing_actual_rates = sorted(
                actual_labor_df[
                    (actual_labor_df["matched"] == True)
                    & (actual_labor_df["has_rate"] == False)
                ]["employee"].unique()
            )

            if unmatched_actual:
                st.error(
                    "These names from the Pay Period Report were not found "
                    "in Team Management: "
                    + ", ".join(unmatched_actual)
                )

            if missing_actual_rates:
                st.error(
                    "These team members do not have compensation effective "
                    "for one or more dates in this report: "
                    + ", ".join(missing_actual_rates)
                )

            valid_actual_df = actual_labor_df[
                actual_labor_df["actual_labor_cost"].notna()
            ].copy()

            report_dates = sorted(
                actual_hours_df["date"].unique()
            )

            # Chick-fil-A is closed on Sundays, but Sunday labor can still occur
            # for cleaning, training, meetings, etc. Always represent the full
            # Sunday-Saturday week so Sunday labor and fixed salary allocation
            # are included even though Sunday sales are always $0.
            first_report_date = parse_date_value(report_dates[0])
            days_since_sunday = (first_report_date.weekday() + 1) % 7
            actual_week_start = first_report_date - timedelta(
                days=days_since_sunday
            )
            actual_dates = [
                actual_week_start + timedelta(days=offset)
                for offset in range(7)
            ]

            st.subheader("Actual Sales")
            st.caption(
                "Enter actual net sales for Monday-Saturday. Sunday sales are "
                "automatically $0 because the store is closed, but Sunday labor "
                "is still included for cleaning, training, meetings, or other work."
            )

            actual_sales_by_date = {}

            sales_columns = st.columns(7)

            for index, actual_date in enumerate(actual_dates):
                with sales_columns[index]:
                    if actual_date.weekday() == 6:
                        st.metric(
                            actual_date.strftime("%a %m/%d"),
                            "$0.00"
                        )
                        st.caption("Closed")
                        actual_sales_by_date[actual_date] = 0.0
                    else:
                        actual_sales_by_date[actual_date] = st.number_input(
                            actual_date.strftime("%a %m/%d"),
                            min_value=0.0,
                            value=0.0,
                            step=500.0,
                            format="%.2f",
                            key=f"actual_sales_{actual_date.isoformat()}"
                        )

            # Fixed salary labor is independent of salary employees' punched hours.
            # Each day's allocation is the sum of all active applicable annual
            # salaries / 52 weeks / 7 days.
            actual_salary_allocations = build_daily_salary_costs(
                actual_dates,
                employees_db,
                salaries_db
            )
            salary_by_date = {
                parse_date_value(row["date"]): float(row["salary_labor_cost"])
                for _, row in actual_salary_allocations.iterrows()
            }

            daily_actual_records = []

            for actual_date in actual_dates:
                day_df = valid_actual_df[
                    valid_actual_df["date"] == actual_date
                ]

                day_all_df = actual_labor_df[
                    actual_labor_df["date"] == actual_date
                ]

                actual_sales = float(
                    actual_sales_by_date.get(actual_date, 0.0)
                )

                hourly_and_extra_labor = float(
                    day_df["actual_labor_cost"].sum()
                )

                salary_labor = float(
                    salary_by_date.get(actual_date, 0.0)
                )

                labor_cost = (
                    hourly_and_extra_labor
                    + salary_labor
                )

                pspt_total = float(
                    day_all_df["pspt_dollars"].sum()
                )

                other_pay_total = float(
                    day_all_df["other_pay_dollars"].sum()
                )

                regular_pay = float(
                    day_df["base_pay"].fillna(0).sum()
                )

                ot_pay = float(
                    day_df["ot_pay"].fillna(0).sum()
                )

                dbl_pay = float(
                    day_df["dbl_pay"].fillna(0).sum()
                )

                hours_worked = float(
                    day_all_df["hours_worked"].sum()
                )

                actual_labor_pct = (
                    labor_cost / actual_sales * 100
                    if actual_sales > 0
                    else None
                )

                workers = sorted(
                    day_all_df["employee"]
                    .dropna()
                    .astype(str)
                    .unique()
                    .tolist()
                )

                daily_actual_records.append({
                    "date": actual_date,
                    "sales": actual_sales,
                    "labor_cost": labor_cost,
                    "labor_pct": actual_labor_pct,
                    "hours_worked": hours_worked,
                    "regular_pay": regular_pay,
                    "ot_pay": ot_pay,
                    "dbl_pay": dbl_pay,
                    "salary_labor": salary_labor,
                    "pspt": pspt_total,
                    "other_pay": other_pay_total,
                    "workers": workers
                })

            daily_actual_df = pd.DataFrame(
                daily_actual_records
            )

            weekly_actual_sales = float(
                daily_actual_df["sales"].sum()
            )

            weekly_actual_labor = float(
                daily_actual_df["labor_cost"].sum()
            )

            weekly_actual_labor_pct = (
                weekly_actual_labor
                / weekly_actual_sales
                * 100
                if weekly_actual_sales > 0
                else None
            )

            weekly_pspt = float(
                daily_actual_df["pspt"].sum()
            )

            weekly_hours = float(
                daily_actual_df["hours_worked"].sum()
            )

            st.divider()
            st.subheader("Weekly Actual Labor")

            a1, a2, a3, a4 = st.columns(4)

            a1.metric(
                "Actual Weekly Sales",
                f"${weekly_actual_sales:,.2f}"
            )

            a2.metric(
                "Actual Weekly Labor",
                f"${weekly_actual_labor:,.2f}"
            )

            a3.metric(
                "Actual Weekly Labor %",
                (
                    f"{weekly_actual_labor_pct:.2f}%"
                    if weekly_actual_labor_pct is not None
                    else "Enter Sales"
                )
            )

            a4.metric(
                "PSPT Premium Pay",
                f"${weekly_pspt:,.2f}"
            )

            st.caption(
                f"Total punched hours in report: {weekly_hours:,.2f}"
            )

            st.divider()
            st.subheader("Daily Actual Labor")

            actual_display = []

            for _, row in daily_actual_df.iterrows():
                actual_display.append({
                    "Day": row["date"].strftime("%A %m/%d"),
                    "Actual Sales": f"${row['sales']:,.2f}",
                    "Actual Labor": f"${row['labor_cost']:,.2f}",
                    "Actual Labor %": (
                        f"{row['labor_pct']:.2f}%"
                        if row["labor_pct"] is not None
                        else "—"
                    ),
                    "Hours Worked": f"{row['hours_worked']:.2f}",
                    "Regular Pay": f"${row['regular_pay']:,.2f}",
                    "OT Pay": f"${row['ot_pay']:,.2f}",
                    "DBL Additional Pay": f"${row['dbl_pay']:,.2f}",
                    "Salary Labor": f"${row['salary_labor']:,.2f}",
                    "PSPT": f"${row['pspt']:,.2f}",
                    "Other Pay": f"${row['other_pay']:,.2f}",
                    "Who Worked": ", ".join(row["workers"])
                })

            st.dataframe(
                pd.DataFrame(actual_display),
                use_container_width=True,
                hide_index=True
            )

            st.divider()
            st.subheader("Save Actual Labor")
            st.caption(
                "Save this completed week to Supabase for weekly and monthly history."
            )

            can_save_actual = (
                not actual_labor_df.empty
                and actual_labor_df["matched"].all()
                and actual_labor_df["has_rate"].all()
                and actual_labor_df["actual_labor_cost"].notna().all()
                and not daily_actual_df.empty
                and (
                    daily_actual_df[
                        daily_actual_df["date"].apply(
                            lambda value: parse_date_value(value).weekday() != 6
                        )
                    ]["sales"] > 0
                ).all()
            )

            if not can_save_actual:
                st.warning(
                    "Resolve unmatched/missing compensation records and enter "
                    "actual sales greater than $0 for Monday-Saturday. Sunday is "
                    "closed, so Sunday sales remain $0 and do not block saving."
                )

            if st.button(
                "💾 Save Final Actual Labor",
                type="primary",
                disabled=not can_save_actual,
                key="save_final_actual_labor"
            ):
                try:
                    save_final_actual_labor(
                        daily_actual_df,
                        actual_labor_df
                    )
                    st.success(
                        "Actual labor was saved successfully and is now "
                        "included in Actual Labor History below."
                    )
                except Exception as e:
                    st.error(f"Actual labor was NOT saved: {e}")

            st.subheader("Employee Actual Labor Detail")

            employee_actual_display = (
                actual_labor_df.copy()
            )

            employee_actual_display["Date"] = (
                employee_actual_display["date"]
                .apply(lambda x: x.strftime("%a %m/%d"))
            )

            employee_actual_display["Rate"] = (
                employee_actual_display["base_rate"]
                .apply(
                    lambda x: (
                        f"${float(x):,.2f}"
                        if pd.notna(x)
                        else "Salary / Missing"
                    )
                )
            )

            for source_col, display_col in [
                ("base_pay", "Regular Pay"),
                ("ot_pay", "OT Pay"),
                ("dbl_pay", "DBL Additional Pay"),
                ("pspt_dollars", "PSPT"),
                ("other_pay_dollars", "Other Pay"),
                ("salary_labor", "Salary Labor"),
                ("actual_labor_cost", "Actual Labor")
            ]:
                employee_actual_display[display_col] = (
                    employee_actual_display[source_col]
                    .apply(
                        lambda x: (
                            f"${float(x):,.2f}"
                            if pd.notna(x)
                            else "Missing Rate"
                        )
                    )
                )

            employee_actual_display["Employee"] = (
                employee_actual_display["employee"]
            )

            employee_actual_display["REG Hours"] = (
                employee_actual_display["reg_hours"]
            )

            employee_actual_display["OT Hours"] = (
                employee_actual_display["ot_hours"]
            )

            employee_actual_display["DBL Hours"] = (
                employee_actual_display["dbl_hours"]
            )

            employee_actual_display["Hours Worked"] = (
                employee_actual_display["hours_worked"]
            )

            st.dataframe(
                employee_actual_display[
                    [
                        "Employee",
                        "Date",
                        "Rate",
                        "REG Hours",
                        "OT Hours",
                        "DBL Hours",
                        "Hours Worked",
                        "Regular Pay",
                        "OT Pay",
                        "DBL Additional Pay",
                        "PSPT",
                        "Other Pay",
                        "Salary Labor",
                        "Actual Labor"
                    ]
                ],
                use_container_width=True,
                hide_index=True
            )

            pspt_rows = actual_labor_df[
                actual_labor_df["pspt_dollars"] > 0
            ].copy()

            if not pspt_rows.empty:
                st.divider()
                st.subheader("PSPT Premium Pay Detail")

                pspt_display = pspt_rows[
                    ["employee", "date", "pspt_dollars"]
                ].copy()

                pspt_display["date"] = (
                    pspt_display["date"]
                    .apply(lambda x: x.strftime("%A %m/%d"))
                )

                pspt_display["pspt_dollars"] = (
                    pspt_display["pspt_dollars"]
                    .apply(lambda x: f"${float(x):,.2f}")
                )

                pspt_display.columns = [
                    "Team Member",
                    "Date",
                    "PSPT Premium"
                ]

                st.dataframe(
                    pspt_display,
                    use_container_width=True,
                    hide_index=True
                )


    st.divider()
    st.divider()
    st.subheader("📚 Actual Labor History")
    st.caption(
        "Saved actual labor stays here in the Actual Labor tab. "
        "Weekly and monthly percentages use your saved actual sales and labor."
    )

    try:
        saved_actual_weeks = get_saved_actual_weeks()
        all_actual_days = get_all_saved_actual_days()
    except Exception as e:
        saved_actual_weeks = []
        all_actual_days = []
        st.warning(
            "Actual labor history could not be loaded. Make sure the "
            "labor_actual_weeks, labor_actual_days, and "
            "labor_actual_employee_days tables exist in Supabase."
        )
        st.caption(str(e))

    if not saved_actual_weeks:
        st.info("No actual labor weeks have been saved yet.")
    else:
        actual_week_options = {}
        for week in saved_actual_weeks:
            start_date = parse_date_value(week["week_start"])
            end_date = parse_date_value(week["week_end"])
            label = (
                f"{start_date.strftime('%b %d, %Y')} – "
                f"{end_date.strftime('%b %d, %Y')}"
            )
            actual_week_options[label] = week

        selected_actual_week_label = st.selectbox(
            "Saved Actual Week",
            list(actual_week_options.keys()),
            key="saved_actual_week"
        )
        selected_actual_week = actual_week_options[
            selected_actual_week_label
        ]
        saved_actual_days = get_saved_actual_days(
            selected_actual_week["id"]
        )

        if saved_actual_days:
            selected_actual_df = pd.DataFrame(saved_actual_days)
            selected_actual_df["work_date"] = pd.to_datetime(
                selected_actual_df["work_date"]
            ).dt.date

            weekly_actual_sales = float(
                selected_actual_df["actual_sales"].astype(float).sum()
            )
            weekly_actual_labor = float(
                selected_actual_df["actual_labor_cost"].astype(float).sum()
            )
            weekly_actual_pct = (
                weekly_actual_labor / weekly_actual_sales * 100
                if weekly_actual_sales > 0
                else 0
            )
            weekly_actual_hours = float(
                selected_actual_df["actual_hours"].astype(float).sum()
            )

            aw1, aw2, aw3, aw4 = st.columns(4)
            aw1.metric(
                "Actual Weekly Sales",
                f"${weekly_actual_sales:,.2f}"
            )
            aw2.metric(
                "Actual Weekly Labor",
                f"${weekly_actual_labor:,.2f}"
            )
            aw3.metric(
                "Actual Weekly Labor %",
                f"{weekly_actual_pct:.2f}%"
            )
            aw4.metric(
                "Actual Weekly Hours",
                f"{weekly_actual_hours:,.2f}"
            )

            actual_history_display = selected_actual_df[
                [
                    "work_date",
                    "actual_sales",
                    "actual_labor_cost",
                    "actual_labor_pct",
                    "actual_hours",
                    "regular_pay",
                    "overtime_pay",
                    "salary_labor",
                    "pspt_pay",
                    "other_pay",
                    "employee_count"
                ]
            ].copy()

            actual_history_display.columns = [
                "Date",
                "Actual Sales",
                "Actual Labor",
                "Labor %",
                "Hours",
                "Regular Pay",
                "OT Pay",
                "Salary Labor",
                "PSPT",
                "Other Pay",
                "Workers"
            ]

            actual_history_display["Date"] = (
                actual_history_display["Date"]
                .apply(lambda value: value.strftime("%a %m/%d/%Y"))
            )

            for money_column in [
                "Actual Sales",
                "Actual Labor",
                "Regular Pay",
                "OT Pay",
                "Salary Labor",
                "PSPT",
                "Other Pay"
            ]:
                actual_history_display[money_column] = (
                    actual_history_display[money_column]
                    .apply(lambda value: f"${float(value):,.2f}")
                )

            actual_history_display["Labor %"] = (
                actual_history_display["Labor %"]
                .apply(
                    lambda value: (
                        f"{float(value):.2f}%"
                        if pd.notna(value)
                        else "—"
                    )
                )
            )

            st.dataframe(
                actual_history_display,
                use_container_width=True,
                hide_index=True
            )

    if all_actual_days:
        all_actual_df = pd.DataFrame(all_actual_days)
        all_actual_df["work_date"] = pd.to_datetime(
            all_actual_df["work_date"]
        ).dt.date

        actual_month_values = sorted(
            {
                (value.year, value.month)
                for value in all_actual_df["work_date"]
            },
            reverse=True
        )

        actual_month_labels = {
            date(year, month, 1).strftime("%B %Y"): (year, month)
            for year, month in actual_month_values
        }

        selected_actual_month_label = st.selectbox(
            "Actual Labor Month",
            list(actual_month_labels.keys()),
            key="saved_actual_month"
        )
        actual_year, actual_month = actual_month_labels[
            selected_actual_month_label
        ]

        month_actual_df = all_actual_df[
            all_actual_df["work_date"].apply(
                lambda value: (
                    value.year == actual_year
                    and value.month == actual_month
                )
            )
        ].copy()

        monthly_actual_sales = float(
            month_actual_df["actual_sales"].astype(float).sum()
        )
        monthly_actual_labor = float(
            month_actual_df["actual_labor_cost"].astype(float).sum()
        )
        monthly_actual_pct = (
            monthly_actual_labor / monthly_actual_sales * 100
            if monthly_actual_sales > 0
            else 0
        )
        monthly_actual_hours = float(
            month_actual_df["actual_hours"].astype(float).sum()
        )

        st.subheader(
            f"📅 Actual Monthly Labor — {selected_actual_month_label}"
        )

        am1, am2, am3, am4 = st.columns(4)
        am1.metric(
            "Actual Monthly Sales",
            f"${monthly_actual_sales:,.2f}"
        )
        am2.metric(
            "Actual Monthly Labor",
            f"${monthly_actual_labor:,.2f}"
        )
        am3.metric(
            "Actual Monthly Labor %",
            f"{monthly_actual_pct:.2f}%"
        )
        am4.metric(
            "Actual Monthly Hours",
            f"{monthly_actual_hours:,.2f}"
        )

        st.caption(
            "Monthly labor % is total saved actual labor divided by "
            "total saved actual sales for that calendar month."
        )



# ============================================================
# TAB 9 — PROJECTED VS ACTUAL COMPARISONS
# ============================================================

with tab9:

    st.subheader("🔄 Projected vs Actual")

    st.caption(
        "Select a saved week to compare the finished projection "
        "with the actual labor results."
    )

    try:
        comparison_projection_weeks = get_saved_projection_weeks()
        comparison_actual_weeks = get_saved_actual_weeks()

        def comparison_sunday_start(value):
            """Normalize any date to the Sunday that starts its business week."""
            value = parse_date_value(value)
            days_since_sunday = (value.weekday() + 1) % 7
            return value - timedelta(days=days_since_sunday)

        # Projections may be saved Monday-Saturday while Actual Labor is
        # saved Sunday-Saturday. Match both records to the same Sunday-start
        # business week instead of requiring identical week_start values.
        projected_by_start = {
            comparison_sunday_start(week["week_start"]): week
            for week in comparison_projection_weeks
        }

        actual_by_start = {
            comparison_sunday_start(week["week_start"]): week
            for week in comparison_actual_weeks
        }

        comparison_week_starts = sorted(
            set(projected_by_start.keys())
            & set(actual_by_start.keys()),
            reverse=True
        )

        if not comparison_week_starts:
            st.info(
                "No completed weeks are available for comparison yet. "
                "A week will appear here once both a saved projection and "
                "saved actual labor exist for the same week."
            )

        else:
            selected_comparison_week = st.selectbox(
                "Select week",
                comparison_week_starts,
                format_func=lambda value: (
                    f"{value.strftime('%a %b %d, %Y')} – "
                    f"{(value + timedelta(days=6)).strftime('%a %b %d, %Y')}"
                ),
                key="comparison_week_selector"
            )

            projected_week = projected_by_start[selected_comparison_week]
            actual_week = actual_by_start[selected_comparison_week]

            projection_days = get_saved_projection_days(
                projected_week["id"]
            )

            actual_days = get_saved_actual_days(
                actual_week["id"]
            )

            weekly_comparison, daily_comparison_df = (
                build_week_comparison(
                    projection_days,
                    actual_days
                )
            )

            if weekly_comparison is None:
                st.warning(
                    "The selected week does not have enough daily data "
                    "to build a comparison."
                )

            else:
                st.markdown("### Week Overview")

                week_overview_df = pd.DataFrame([
                    {
                        "Metric": "Sales",
                        "Projected": f"${weekly_comparison['projected_sales']:,.2f}",
                        "Actual": f"${weekly_comparison['actual_sales']:,.2f}",
                        "Difference": format_signed_currency(
                            weekly_comparison["sales_difference"]
                        )
                    },
                    {
                        "Metric": "Labor Cost",
                        "Projected": f"${weekly_comparison['projected_labor']:,.2f}",
                        "Actual": f"${weekly_comparison['actual_labor']:,.2f}",
                        "Difference": format_signed_currency(
                            weekly_comparison["labor_difference"]
                        )
                    },
                    {
                        "Metric": "Labor %",
                        "Projected": format_pct(
                            weekly_comparison["projected_labor_pct"]
                        ),
                        "Actual": format_pct(
                            weekly_comparison["actual_labor_pct"]
                        ),
                        "Difference": format_signed_pct(
                            weekly_comparison["labor_pct_difference"]
                        )
                    },
                    {
                        "Metric": "Paid Hours",
                        "Projected": f"{weekly_comparison['projected_hours']:,.2f}",
                        "Actual": f"{weekly_comparison['actual_hours']:,.2f}",
                        "Difference": format_signed_number(
                            weekly_comparison["hours_difference"]
                        )
                    }
                ])

                st.dataframe(
                    week_overview_df,
                    use_container_width=True,
                    hide_index=True
                )

                st.caption(
                    "Difference = Actual − Projected. "
                    "For labor cost, labor %, and hours, a positive difference "
                    "means actual was higher than projected."
                )

                st.divider()
                st.markdown("### Daily Comparison")

                daily_display_df = daily_comparison_df.copy()

                daily_display_df["Day"] = (
                    daily_display_df["date"]
                    .apply(lambda value: value.strftime("%A"))
                )

                daily_display_df["Date"] = (
                    daily_display_df["date"]
                    .apply(lambda value: value.strftime("%m/%d/%Y"))
                )

                daily_display_df["Projected Sales"] = (
                    daily_display_df["projected_sales"]
                    .apply(lambda value: f"${value:,.2f}")
                )

                daily_display_df["Actual Sales"] = (
                    daily_display_df["actual_sales"]
                    .apply(lambda value: f"${value:,.2f}")
                )

                daily_display_df["Sales Difference"] = (
                    daily_display_df["sales_difference"]
                    .apply(format_signed_currency)
                )

                daily_display_df["Projected Labor"] = (
                    daily_display_df["projected_labor"]
                    .apply(lambda value: f"${value:,.2f}")
                )

                daily_display_df["Actual Labor"] = (
                    daily_display_df["actual_labor"]
                    .apply(lambda value: f"${value:,.2f}")
                )

                daily_display_df["Labor Difference"] = (
                    daily_display_df["labor_difference"]
                    .apply(format_signed_currency)
                )

                daily_display_df["Projected Labor %"] = (
                    daily_display_df["projected_labor_pct"]
                    .apply(format_pct)
                )

                daily_display_df["Actual Labor %"] = (
                    daily_display_df["actual_labor_pct"]
                    .apply(format_pct)
                )

                daily_display_df["Labor % Difference"] = (
                    daily_display_df["labor_pct_difference"]
                    .apply(format_signed_pct)
                )

                daily_display_df["Projected Hours"] = (
                    daily_display_df["projected_hours"]
                    .apply(lambda value: f"{value:,.2f}")
                )

                daily_display_df["Actual Hours"] = (
                    daily_display_df["actual_hours"]
                    .apply(lambda value: f"{value:,.2f}")
                )

                daily_display_df["Hours Difference"] = (
                    daily_display_df["hours_difference"]
                    .apply(format_signed_number)
                )

                st.dataframe(
                    daily_display_df[
                        [
                            "Day",
                            "Date",
                            "Projected Sales",
                            "Actual Sales",
                            "Sales Difference",
                            "Projected Labor",
                            "Actual Labor",
                            "Labor Difference",
                            "Projected Labor %",
                            "Actual Labor %",
                            "Labor % Difference",
                            "Projected Hours",
                            "Actual Hours",
                            "Hours Difference"
                        ]
                    ],
                    use_container_width=True,
                    hide_index=True
                )

                st.divider()
                st.markdown("### 👥 Team Member Changes")

                projection_employee_days = (
                    get_saved_projection_employee_days(
                        projected_week["id"]
                    )
                )

                if not projection_employee_days:
                    st.info(
                        "This projection was saved before employee-level "
                        "projection snapshots were added. Re-save the original "
                        "projection once to enable team-member comparisons."
                    )
                else:
                    employee_comparison_df = build_employee_comparison(
                        projection_employee_days,
                        actual_week["id"]
                    )

                    if employee_comparison_df.empty:
                        st.info(
                            "No employee-level comparison data is available "
                            "for this week."
                        )
                    else:
                        total_ot = float(
                            employee_comparison_df["actual_ot"].sum()
                        )
                        total_pspt = float(
                            employee_comparison_df["actual_pspt"].sum()
                        )
                        total_other = float(
                            employee_comparison_df["actual_other_pay"].sum()
                        )
                        total_premiums = (
                            total_ot + total_pspt + total_other
                        )

                        premium_col1, premium_col2, premium_col3, premium_col4 = (
                            st.columns(4)
                        )

                        premium_col1.metric(
                            "OT Premium / OT Pay",
                            f"${total_ot:,.2f}"
                        )
                        premium_col2.metric(
                            "PSPT Premiums",
                            f"${total_pspt:,.2f}"
                        )
                        premium_col3.metric(
                            "Other Pay",
                            f"${total_other:,.2f}"
                        )
                        premium_col4.metric(
                            "Total Extra Pay",
                            f"${total_premiums:,.2f}"
                        )

                        st.caption(
                            "Use the employee table below to see who worked "
                            "more or fewer hours than projected, who was added "
                            "or removed, and how those changes affected labor."
                        )

                        employee_display_df = pd.DataFrame({
                            "Team Member": employee_comparison_df["employee"],
                            "Change": employee_comparison_df["schedule_change"],
                            "Projected Hrs": employee_comparison_df[
                                "projected_hours"
                            ].map(lambda value: f"{value:,.2f}"),
                            "Actual Hrs": employee_comparison_df[
                                "actual_hours"
                            ].map(lambda value: f"{value:,.2f}"),
                            "Hrs Δ": employee_comparison_df[
                                "hours_difference"
                            ].map(format_signed_number),
                            "Projected Rate": employee_comparison_df[
                                "projected_rate"
                            ].map(
                                lambda value: (
                                    f"${float(value):,.2f}"
                                    if pd.notna(value)
                                    else "Salary / N/A"
                                )
                            ),
                            "Actual Rate": employee_comparison_df[
                                "actual_rate"
                            ].map(
                                lambda value: (
                                    f"${float(value):,.2f}"
                                    if pd.notna(value)
                                    else "Salary / N/A"
                                )
                            ),
                            "Projected Labor": employee_comparison_df[
                                "projected_labor"
                            ].map(lambda value: f"${value:,.2f}"),
                            "Actual Labor": employee_comparison_df[
                                "actual_labor"
                            ].map(lambda value: f"${value:,.2f}"),
                            "Labor Δ": employee_comparison_df[
                                "labor_difference"
                            ].map(format_signed_currency),
                            "OT": employee_comparison_df[
                                "actual_ot"
                            ].map(lambda value: f"${value:,.2f}"),
                            "PSPT": employee_comparison_df[
                                "actual_pspt"
                            ].map(lambda value: f"${value:,.2f}"),
                            "Other Pay": employee_comparison_df[
                                "actual_other_pay"
                            ].map(lambda value: f"${value:,.2f}")
                        })

                        st.dataframe(
                            employee_display_df,
                            use_container_width=True,
                            hide_index=True
                        )

                        changed_people = employee_comparison_df[
                            (
                                employee_comparison_df["hours_difference"].abs()
                                > 0.01
                            )
                            | (
                                employee_comparison_df["labor_difference"].abs()
                                > 0.01
                            )
                            | (employee_comparison_df["premium_total"] > 0)
                        ]

                        if not changed_people.empty:
                            st.markdown("#### Biggest Labor Changes")

                            biggest_changes = (
                                changed_people.assign(
                                    absolute_labor_change=(
                                        changed_people[
                                            "labor_difference"
                                        ].abs()
                                    )
                                )
                                .sort_values(
                                    "absolute_labor_change",
                                    ascending=False
                                )
                                .head(10)
                            )

                            change_summary_df = pd.DataFrame({
                                "Team Member": biggest_changes["employee"],
                                "Hours Δ": biggest_changes[
                                    "hours_difference"
                                ].map(format_signed_number),
                                "Labor Δ": biggest_changes[
                                    "labor_difference"
                                ].map(format_signed_currency),
                                "Extra Pay": biggest_changes[
                                    "premium_total"
                                ].map(lambda value: f"${value:,.2f}"),
                                "What Changed": biggest_changes[
                                    "schedule_change"
                                ]
                            })

                            st.dataframe(
                                change_summary_df,
                                use_container_width=True,
                                hide_index=True
                            )

    except Exception as comparison_error:
        st.error(
            f"Could not load projected vs actual comparison: "
            f"{comparison_error}"
        )



# ============================================================
# SCHEDULE UPLOAD
# ============================================================

st.divider()

st.subheader(
    "Weekly Schedule"
)

uploaded_file = (
    st.file_uploader(
        "Upload Weekly Schedule",
        type=[
            "xls",
            "xlsx",
            "html",
            "htm"
        ],
        help=(
            "Upload the Extended Schedule Report "
            "from your scheduling system."
        )
    )
)


# ============================================================
# NO SCHEDULE UPLOADED
# ============================================================

if uploaded_file is None:

    with tab1:
        st.info(
            "Upload a weekly schedule to view "
            "the schedule overview."
        )

    with tab2:
        st.info(
            "Upload a weekly schedule to analyze staffing."
        )

    with tab3:
        st.info(
            "Upload a weekly schedule to calculate "
            "employee hours."
        )

    with tab4:
        st.info(
            "Upload a weekly schedule to calculate "
            "projected labor."
        )

    with tab6:
        st.info(
            "Upload a weekly schedule to run "
            "schedule checks."
        )

    st.stop()


# ============================================================
# PARSE UPLOADED SCHEDULE
# ============================================================

try:

    schedule_df = (
        parse_schedule(
            uploaded_file.getvalue()
        )
    )

except Exception as e:

    st.error(
        "The schedule could not be parsed."
    )

    st.exception(
        e
    )

    st.stop()


if schedule_df.empty:

    st.error(
        "No shifts could be found in this schedule."
    )

    st.stop()


dates = sorted(
    schedule_df[
        "date"
    ].unique()
)

employees = sorted(
    schedule_df[
        "employee"
    ].unique()
)

employee_hours_df = (
    build_employee_hours_table(
        schedule_df
    )
)


# ============================================================
# BUILD LABOR DATA
# ============================================================

if not supabase_error:

    labor_df = (
        build_labor_cost_data(
            employee_hours_df,
            employees_db,
            rates_db,
            salaries_db,
            aliases_db
        )
    )

else:

    labor_df = (
        pd.DataFrame()
    )


# ============================================================
# SIDEBAR
# ============================================================

st.sidebar.header(
    "Schedule Settings"
)

selected_date = (
    st.sidebar.selectbox(
        "Analyze Date",
        dates,
        format_func=lambda x:
            x.strftime(
                "%A, %B %d, %Y"
            )
    )
)

interval_label = (
    st.sidebar.selectbox(
        "Staffing Interval",
        [
            "1 Hour",
            "30 Minutes",
            "15 Minutes"
        ]
    )
)

interval_map = {
    "1 Hour": 60,
    "30 Minutes": 30,
    "15 Minutes": 15
}

interval_minutes = (
    interval_map[
        interval_label
    ]
)


# ============================================================
# TAB 1 — OVERVIEW
# ============================================================

with tab1:
    st.header(
        "Schedule Overview"
    )

    st.subheader(
        selected_date.strftime(
            "%A, %B %d, %Y"
        )
    )

    selected_day_df = (
        schedule_df[
            schedule_df[
                "date"
            ]
            == selected_date
        ]
        .copy()
    )

    selected_day_hours = (
        employee_hours_df[
            employee_hours_df[
                "date"
            ]
            == selected_date
        ]
    )

    c1, c2, c3, c4 = (
        st.columns(4)
    )

    c1.metric(
        "Employees Working",
        selected_day_df[
            "employee"
        ].nunique()
    )

    c2.metric(
        "Pre-Break Hours",
        f"{selected_day_hours['pre_break_hours'].sum():.2f}"
    )

    c3.metric(
        "Meal Time",
        (
            f"{selected_day_hours['break_minutes'].sum() / 60:.2f} hrs"
        )
    )

    c4.metric(
        "Projected Paid Hours",
        f"{selected_day_hours['post_break_hours'].sum():.2f}"
    )

    st.subheader(
        "Assignments"
    )

    display_df = (
        selected_day_df[
            [
                "employee",
                "department",
                "job_type",
                "start_time",
                "end_time",
                "hours"
            ]
        ]
        .copy()
    )

    display_df[
        "start_time"
    ] = display_df[
        "start_time"
    ].apply(
        format_time
    )

    display_df[
        "end_time"
    ] = display_df[
        "end_time"
    ].apply(
        format_time
    )

    display_df.columns = [
        "Employee",
        "Department",
        "Job Type",
        "Start",
        "End",
        "Assignment Hours"
    ]

    st.dataframe(
        display_df,
        use_container_width=True,
        hide_index=True
    )


# ============================================================
# TAB 2 — STAFFING
# ============================================================

with tab2:
    st.header(
        "Staffing by Time"
    )

    st.subheader(
        selected_date.strftime(
            "%A, %B %d, %Y"
        )
    )

    day_df = (
        schedule_df[
            schedule_df[
                "date"
            ]
            == selected_date
        ]
    )

    staffing_df = (
        build_staffing_table(
            day_df,
            interval_minutes
        )
    )

    if staffing_df.empty:

        st.info(
            "No staffing data was found for this day."
        )

    else:

        st.caption(
            "Body count is fractional based on how much "
            "of each interval each employee is scheduled."
        )

        st.caption(
            "Projected meal periods are not removed from "
            "staffing coverage because the schedule does "
            "not specify when meals are actually taken."
        )

        st.subheader(
            "Total Staffing"
        )

        st.line_chart(
            staffing_df[
                [
                    "Time",
                    "Total Employees"
                ]
            ]
            .set_index(
                "Time"
            )
        )

        st.subheader(
            "Staffing by Job Type"
        )

        st.dataframe(
            staffing_df,
            use_container_width=True,
            hide_index=True
        )

        job_columns = [
            column
            for column
            in staffing_df.columns
            if column not in [
                "Time",
                "Total Employees"
            ]
        ]

        if job_columns:

            selected_jobs = (
                st.multiselect(
                    "Show Job Types",
                    job_columns,
                    default=job_columns
                )
            )

            if selected_jobs:

                st.line_chart(
                    staffing_df[
                        ["Time"]
                        + selected_jobs
                    ]
                    .set_index(
                        "Time"
                    )
                )


# ============================================================
# TAB 3 — EMPLOYEE HOURS
# ============================================================

with tab3:
    st.header(
        "Employee Hours"
    )

    st.subheader(
        "Scheduled Hours — Before Breaks"
    )

    pre_break_pivot = (
        create_hours_pivot(
            employee_hours_df,
            "pre_break_hours"
        )
    )

    st.dataframe(
        pre_break_pivot,
        use_container_width=True
    )

    st.divider()

    st.subheader(
        "Projected Meal Periods"
    )

    break_pivot = (
        create_hours_pivot(
            employee_hours_df,
            "break_minutes"
        )
    )

    break_pivot = (
        break_pivot.rename(
            columns={
                "Weekly Total":
                    "Weekly Meal Minutes"
            }
        )
    )

    st.dataframe(
        break_pivot,
        use_container_width=True
    )

    st.divider()

    st.subheader(
        "Projected Paid Hours — After Breaks"
    )

    post_break_pivot = (
        create_hours_pivot(
            employee_hours_df,
            "post_break_hours"
        )
    )

    st.dataframe(
        post_break_pivot,
        use_container_width=True
    )

    st.divider()

    st.subheader(
        "Employee Detail"
    )

    selected_employee = (
        st.selectbox(
            "Select Employee",
            employees,
            key="employee_hours_detail"
        )
    )

    employee_detail = (
        employee_hours_df[
            employee_hours_df[
                "employee"
            ]
            == selected_employee
        ]
        .copy()
    )

    c1, c2, c3 = (
        st.columns(3)
    )

    c1.metric(
        "Pre-Break Hours",
        (
            f"{employee_detail['pre_break_hours'].sum():.2f}"
        )
    )

    c2.metric(
        "Meal Minutes",
        (
            f"{employee_detail['break_minutes'].sum():.0f}"
        )
    )

    c3.metric(
        "Projected Paid Hours",
        (
            f"{employee_detail['post_break_hours'].sum():.2f}"
        )
    )

    detail_display = (
        employee_detail.copy()
    )

    detail_display[
        "date"
    ] = detail_display[
        "date"
    ].apply(
        lambda x:
            x.strftime(
                "%A %m/%d"
            )
    )

    detail_display.columns = [
        "Employee",
        "Date",
        "Pre-Break Hours",
        "Meal Minutes",
        "Projected Paid Hours"
    ]

    st.dataframe(
        detail_display,
        use_container_width=True,
        hide_index=True
    )


# ============================================================
# TAB 4 — LABOR PLANNER
# ============================================================

with tab4:
    st.header(
        "💰 Labor Planner"
    )

    if supabase_error:

        st.error(
            "Could not retrieve pay data from Supabase."
        )

        st.exception(
            supabase_error
        )

    elif labor_df.empty:

        st.warning(
            "No labor data could be calculated."
        )

    else:

        # ====================================================
        # CHECK DATABASE MATCHES
        # ====================================================

        unmatched_names = sorted(
            labor_df[
                labor_df[
                    "matched"
                ]
                == False
            ][
                "employee"
            ]
            .unique()
        )

        missing_rates = sorted(
            labor_df[
                (labor_df["matched"] == True)
                & (labor_df["has_rate"] == False)
            ]["employee"]
            .unique()
        )

        if unmatched_names:

            st.error(
                "These scheduled employees are not "
                "in the labor database:"
            )

            st.write(
                ", ".join(
                    unmatched_names
                )
            )

            st.caption(
                "Add them under Team Management. "
                "Their labor is not included in the totals below."
            )

        if missing_rates:

            st.error(
                "These employees do not have compensation "
                "effective for their scheduled date:"
            )

            st.write(", ".join(missing_rates))

            st.caption(
                "Add an hourly rate or salary under Team Management. "
                "Their labor is not included in the totals below."
            )

        valid_labor_df = (
            labor_df[
                labor_df[
                    "labor_cost"
                ]
                .notna()
            ]
            .copy()
        )

        if valid_labor_df.empty:

            st.warning(
                "Add the missing employees/pay rates "
                "before calculating labor."
            )

        else:

            # ================================================
            # TARGET LABOR
            # ================================================

            st.subheader(
                "Labor Target"
            )

            target_labor_pct = (
                st.number_input(
                    "Target Labor %",
                    min_value=0.1,
                    max_value=100.0,
                    value=19.0,
                    step=0.1,
                    format="%.1f",
                    key="target_labor_pct"
                )
            )

            st.caption(
                "The target is used for both the "
                "daily and weekly calculations."
            )

            st.divider()

            # ================================================
            # SALES INPUTS
            # ================================================

            st.subheader(
                "Projected Sales"
            )

            st.caption(
                "Enter projected sales for each day. "
                "Weekly sales are calculated automatically."
            )

            projected_sales = {}

            first_dates = (
                dates[:4]
            )

            if first_dates:

                first_cols = (
                    st.columns(
                        len(
                            first_dates
                        )
                    )
                )

                for (
                    col,
                    schedule_date
                ) in zip(
                    first_cols,
                    first_dates
                ):

                    with col:

                        projected_sales[
                            schedule_date
                        ] = st.number_input(
                            schedule_date.strftime(
                                "%A %m/%d"
                            ),
                            min_value=0.0,
                            value=0.0,
                            step=500.0,
                            format="%.2f",
                            key=(
                                "sales_"
                                + schedule_date.isoformat()
                            )
                        )

            second_dates = (
                dates[4:7]
            )

            if second_dates:

                second_cols = (
                    st.columns(
                        len(
                            second_dates
                        )
                    )
                )

                for (
                    col,
                    schedule_date
                ) in zip(
                    second_cols,
                    second_dates
                ):

                    with col:

                        projected_sales[
                            schedule_date
                        ] = st.number_input(
                            schedule_date.strftime(
                                "%A %m/%d"
                            ),
                            min_value=0.0,
                            value=0.0,
                            step=500.0,
                            format="%.2f",
                            key=(
                                "sales_"
                                + schedule_date.isoformat()
                            )
                        )

            # ================================================
            # BUILD DAILY LABOR PLAN
            # ================================================

            daily_labor = (
                valid_labor_df
                .groupby("date", as_index=False)
                .agg(
                    paid_hours=("paid_hours", "sum"),
                    hourly_labor_cost=("labor_cost", "sum")
                )
            )

            daily_salary_df = build_daily_salary_costs(
                dates,
                employees_db,
                salaries_db
            )

            missing_salary_names = sorted(
                {
                    name
                    for names in daily_salary_df["missing_salary_names"]
                    for name in names
                }
            )

            if missing_salary_names:
                st.error(
                    "These salaried employees do not have a salary "
                    "effective for one or more days in this schedule:"
                )
                st.write(", ".join(missing_salary_names))

            daily_records = []

            for schedule_date in dates:

                rows = (
                    daily_labor[
                        daily_labor[
                            "date"
                        ]
                        == schedule_date
                    ]
                )

                if rows.empty:
                    paid_hours = 0
                    hourly_labor_cost = 0
                else:
                    paid_hours = float(rows.iloc[0]["paid_hours"])
                    hourly_labor_cost = float(rows.iloc[0]["hourly_labor_cost"])

                salary_rows = daily_salary_df[
                    daily_salary_df["date"] == schedule_date
                ]

                salary_labor_cost = (
                    float(salary_rows.iloc[0]["salary_labor_cost"])
                    if not salary_rows.empty
                    else 0.0
                )

                labor_cost = hourly_labor_cost + salary_labor_cost

                sales = float(
                    projected_sales.get(
                        schedule_date,
                        0
                    )
                )

                sales_needed = (
                    labor_cost
                    /
                    (
                        target_labor_pct
                        / 100
                    )
                )

                if sales > 0:

                    labor_pct = (
                        labor_cost
                        / sales
                        * 100
                    )

                else:

                    labor_pct = None

                daily_records.append(
                    {
                        "date":
                            schedule_date,

                        "paid_hours":
                            paid_hours,

                        "hourly_labor_cost":
                            hourly_labor_cost,

                        "salary_labor_cost":
                            salary_labor_cost,

                        "labor_cost":
                            labor_cost,

                        "sales":
                            sales,

                        "sales_needed":
                            sales_needed,

                        "labor_pct":
                            labor_pct
                    }
                )

            daily_plan_df = (
                pd.DataFrame(
                    daily_records
                )
            )

            st.divider()

            st.subheader("💾 Save Finished Projection")
            st.caption(
                "Only click this when this is the finished schedule and your "
                "projected sales are final. Saving the same week again replaces "
                "the previously saved snapshot for that week."
            )

            all_sales_entered = bool(
                (daily_plan_df["sales"] > 0).all()
            )

            if not all_sales_entered:
                st.info(
                    "Enter projected sales for every day before saving "
                    "the finished weekly projection."
                )

            save_projection_button = st.button(
                "💾 Save Final Weekly Projection",
                type="primary",
                disabled=not all_sales_entered,
                key="save_final_weekly_projection"
            )

            if save_projection_button:
                try:
                    save_final_projection(
                        daily_plan_df,
                        schedule_df,
                        target_labor_pct
                    )
                    clear_projection_cache()
                    st.success(
                        "Finished projection saved. You can open it anytime "
                        "under 📁 Saved Projections."
                    )
                except Exception as e:
                    st.error(f"Projection was NOT saved: {e}")

            st.divider()

            planning_view = (
                st.radio(
                    "Planning View",
                    [
                        "Day",
                        "Week"
                    ],
                    horizontal=True,
                    key="planning_view"
                )
            )

            # ================================================
            # DAY VIEW
            # ================================================

            if planning_view == "Day":

                st.subheader(
                    selected_date.strftime(
                        "%A, %B %d, %Y"
                    )
                )

                matching_day = (
                    daily_plan_df[
                        daily_plan_df[
                            "date"
                        ]
                        == selected_date
                    ]
                )

                if matching_day.empty:

                    st.info(
                        "No labor information was found "
                        "for this day."
                    )

                else:

                    day_row = (
                        matching_day.iloc[0]
                    )

                    c1, c2, c3, c4, c5 = (
                        st.columns(5)
                    )

                    c1.metric(
                        "Paid Hours",
                        (
                            f"{day_row['paid_hours']:.2f}"
                        )
                    )

                    c2.metric(
                        "Hourly Labor",
                        f"${day_row['hourly_labor_cost']:,.2f}"
                    )

                    c3.metric(
                        "Salary Labor",
                        f"${day_row['salary_labor_cost']:,.2f}"
                    )

                    c4.metric(
                        (
                            f"Sales Needed @ "
                            f"{target_labor_pct:.1f}%"
                        ),
                        (
                            f"${day_row['sales_needed']:,.2f}"
                        )
                    )

                    if pd.notna(
                        day_row[
                            "labor_pct"
                        ]
                    ):

                        c5.metric(
                            "Projected Labor %",
                            (
                                f"{day_row['labor_pct']:.2f}%"
                            )
                        )

                        st.write(
                            "Projected Sales: "
                            f"**${day_row['sales']:,.2f}**"
                        )

                        if (
                            day_row[
                                "labor_pct"
                            ]
                            <= target_labor_pct
                        ):

                            difference = (
                                target_labor_pct
                                - day_row[
                                    "labor_pct"
                                ]
                            )

                            st.success(
                                f"Projected labor is "
                                f"{day_row['labor_pct']:.2f}%, "
                                f"which is {difference:.2f} "
                                f"percentage points below "
                                f"your {target_labor_pct:.1f}% target."
                            )

                        else:

                            extra_sales = max(
                                day_row[
                                    "sales_needed"
                                ]
                                - day_row[
                                    "sales"
                                ],
                                0
                            )

                            st.warning(
                                f"Projected labor is "
                                f"{day_row['labor_pct']:.2f}%. "
                                f"You need approximately "
                                f"${extra_sales:,.2f} more in sales "
                                f"to reach {target_labor_pct:.1f}%."
                            )

                    else:

                        c5.metric(
                            "Projected Labor %",
                            "—"
                        )

                        st.info(
                            "Enter projected sales for this day "
                            "to calculate the labor percentage."
                        )

            # ================================================
            # WEEK VIEW
            # ================================================

            else:

                st.subheader(
                    "Weekly Labor Plan"
                )

                weekly_hours = float(
                    daily_plan_df[
                        "paid_hours"
                    ].sum()
                )

                weekly_hourly_labor = float(
                    daily_plan_df["hourly_labor_cost"].sum()
                )

                weekly_salary_labor = float(
                    daily_plan_df["salary_labor_cost"].sum()
                )

                weekly_labor = float(
                    daily_plan_df["labor_cost"].sum()
                )

                weekly_sales = float(
                    daily_plan_df[
                        "sales"
                    ].sum()
                )

                weekly_sales_needed = (
                    weekly_labor
                    /
                    (
                        target_labor_pct
                        / 100
                    )
                )

                if weekly_sales > 0:

                    weekly_pct = (
                        weekly_labor
                        / weekly_sales
                        * 100
                    )

                else:

                    weekly_pct = None

                c1, c2, c3, c4, c5, c6 = st.columns(6)

                c1.metric("Weekly Paid Hours", f"{weekly_hours:.2f}")
                c2.metric("Hourly Labor", f"${weekly_hourly_labor:,.2f}")
                c3.metric("Salary Labor", f"${weekly_salary_labor:,.2f}")
                c4.metric("Total Labor", f"${weekly_labor:,.2f}")
                c5.metric("Projected Weekly Sales", f"${weekly_sales:,.2f}")

                if weekly_pct is not None:
                    c6.metric("Weekly Labor %", f"{weekly_pct:.2f}%")
                else:
                    c6.metric("Weekly Labor %", "—")

                st.metric(
                    (
                        f"Weekly Sales Needed @ "
                        f"{target_labor_pct:.1f}%"
                    ),
                    f"${weekly_sales_needed:,.2f}"
                )

                if weekly_pct is not None:

                    if (
                        weekly_pct
                        <= target_labor_pct
                    ):

                        difference = (
                            target_labor_pct
                            - weekly_pct
                        )

                        st.success(
                            f"Projected weekly labor is "
                            f"{weekly_pct:.2f}%, which is "
                            f"{difference:.2f} percentage points "
                            f"below your target."
                        )

                    else:

                        additional_sales = max(
                            weekly_sales_needed
                            - weekly_sales,
                            0
                        )

                        st.warning(
                            f"Projected weekly labor is "
                            f"{weekly_pct:.2f}%. "
                            f"You need approximately "
                            f"${additional_sales:,.2f} more "
                            f"weekly sales to reach "
                            f"{target_labor_pct:.1f}%."
                        )

                # ============================================
                # DAILY BREAKDOWN
                # ============================================

                st.subheader(
                    "Daily Breakdown"
                )

                daily_display = []

                for _, row in (
                    daily_plan_df.iterrows()
                ):

                    if pd.notna(
                        row[
                            "labor_pct"
                        ]
                    ):

                        labor_pct_text = (
                            f"{row['labor_pct']:.2f}%"
                        )

                        if (
                            row[
                                "labor_pct"
                            ]
                            <= target_labor_pct
                        ):

                            status = (
                                "At / Below Target"
                            )

                        else:

                            status = (
                                "Above Target"
                            )

                    else:

                        labor_pct_text = "—"
                        status = "Enter Sales"

                    daily_display.append(
                        {
                            "Day":
                                row[
                                    "date"
                                ].strftime(
                                    "%A %m/%d"
                                ),

                            "Paid Hours":
                                round(
                                    row[
                                        "paid_hours"
                                    ],
                                    2
                                ),

                            "Hourly Labor":
                                f"${row['hourly_labor_cost']:,.2f}",

                            "Salary Labor":
                                f"${row['salary_labor_cost']:,.2f}",

                            "Total Labor":
                                f"${row['labor_cost']:,.2f}",

                            "Projected Sales":
                                (
                                    f"${row['sales']:,.2f}"
                                ),

                            "Labor %":
                                labor_pct_text,

                            (
                                f"Sales Needed @ "
                                f"{target_labor_pct:.1f}%"
                            ):
                                (
                                    f"${row['sales_needed']:,.2f}"
                                ),

                            "Status":
                                status
                        }
                    )

                st.dataframe(
                    pd.DataFrame(
                        daily_display
                    ),
                    use_container_width=True,
                    hide_index=True
                )


# ============================================================
# TAB 6 — SCHEDULE CHECKS
# ============================================================

with tab6:
    st.header(
        "Schedule Checks"
    )

    st.subheader(
        selected_date.strftime(
            "%A, %B %d, %Y"
        )
    )

    st.info(
        "This currently contains basic schedule-quality "
        "checks. Additional Fair Workweek, minor, meeting, "
        "availability, and internal rules can be added later."
    )

    issues_df = (
        run_schedule_checks(
            schedule_df
        )
    )

    if issues_df.empty:

        st.success(
            "No current schedule warnings detected."
        )

    else:

        day_issues = (
            issues_df[
                issues_df[
                    "date"
                ]
                == selected_date
            ]
        )

        if day_issues.empty:

            st.success(
                "No current warnings for this day."
            )

        else:

            display_issues = (
                day_issues[
                    [
                        "employee",
                        "type",
                        "severity",
                        "details"
                    ]
                ]
                .copy()
            )

            display_issues.columns = [
                "Employee",
                "Issue",
                "Severity",
                "Details"
            ]

            st.dataframe(
                display_issues,
                use_container_width=True,
                hide_index=True
            )


# ============================================================
# DEVELOPER DATA
# ============================================================

st.divider()

with st.expander(
    "🛠 Developer: View Parsed Shift Data"
):

    debug_df = (
        schedule_df.copy()
    )

    debug_df[
        "start_time"
    ] = debug_df[
        "start_time"
    ].apply(
        format_time
    )

    debug_df[
        "end_time"
    ] = debug_df[
        "end_time"
    ].apply(
        format_time
    )

    st.dataframe(
        debug_df,
        use_container_width=True,
        hide_index=True
    )