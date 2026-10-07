import streamlit as st
import pandas as pd
import re
import io
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
        .select("id, employee_id, hourly_rate, effective_date, created_at")
        .execute()
    )

    salary_response = (
        supabase
        .table("labor_salaries")
        .select("id, employee_id, annual_salary, effective_date, created_at")
        .execute()
    )

    return (
        employee_response.data or [],
        rate_response.data or [],
        salary_response.data or []
    )


def clear_labor_cache():
    get_labor_database.clear()


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
    salaries_db
):

    employee_lookup = {
        normalize_name(employee["employee_name"]): employee
        for employee in employees_db
    }

    records = []

    for _, row in employee_hours_df.iterrows():

        schedule_name = row["employee"].strip()
        employee_data = employee_lookup.get(normalize_name(schedule_name))

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


def clear_projection_cache():
    get_saved_projection_weeks.clear()
    get_saved_projection_days.clear()


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

    employees_db, rates_db, salaries_db = (
        get_labor_database()
    )

    supabase_error = None

except Exception as e:

    employees_db = []
    rates_db = []
    salaries_db = []
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
    tab7
) = st.tabs(
    [
        "📊 Overview",
        "👥 Staffing",
        "⏱ Employee Hours",
        "💰 Labor Planner",
        "👤 Team Management",
        "⚠️ Schedule Checks",
        "📁 Saved Projections"
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
        add_tab, pay_tab, salary_tab, team_tab = st.tabs(
            [
                "➕ Add Team Member",
                "💵 Hourly Pay Changes",
                "💼 Salary Management",
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
                    employees_db, rates_db, salaries_db = get_labor_database()
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
                        employees_db, rates_db, salaries_db = get_labor_database()
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
                        employees_db, rates_db, salaries_db = get_labor_database()
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
        "Open a finished labor projection at any time. "
        "Saving the same week again intentionally replaces that week's snapshot."
    )

    if supabase_error:
        st.error("Could not connect to Supabase.")
    else:
        try:
            saved_weeks = get_saved_projection_weeks()
        except Exception as e:
            saved_weeks = []
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
                saved_days = get_saved_projection_days(
                    selected_saved_week["id"]
                )
            except Exception as e:
                saved_days = []
                st.error(f"Could not load this saved projection: {e}")

            if saved_days:
                target_display = float(
                    selected_saved_week["target_labor_pct"]
                )

                c1, c2 = st.columns(2)
                c1.metric(
                    "Saved Target Labor %",
                    f"{target_display:.2f}%"
                )
                c2.write(
                    "Saved: "
                    f"**{selected_saved_week.get('saved_at', '')}**"
                )

                saved_display = []

                for row in saved_days:
                    employees_list = row.get("scheduled_employees") or []
                    employees_text = (
                        employees_list
                        if isinstance(employees_list, str)
                        else ", ".join(employees_list)
                    )

                    anticipated = row.get("anticipated_labor_pct")
                    sales_needed_pct = row.get(
                        "sales_needed_pct_of_projection"
                    )
                    additional_pct = row.get("additional_sales_pct")

                    saved_display.append({
                        "Day": parse_date_value(
                            row["schedule_date"]
                        ).strftime("%A %m/%d"),
                        "Projected Sales": (
                            f"${float(row['projected_sales']):,.2f}"
                        ),
                        "Anticipated Labor %": (
                            f"{float(anticipated):.2f}%"
                            if anticipated is not None else "—"
                        ),
                        (
                            f"Sales Needed @ "
                            f"{float(row['target_labor_pct']):.1f}%"
                        ): (
                            f"${float(row['sales_needed_at_target']):,.2f}"
                        ),
                        "Needed Sales as % of Projection": (
                            f"{float(sales_needed_pct):.2f}%"
                            if sales_needed_pct is not None else "—"
                        ),
                        "Additional Sales Needed": (
                            f"${float(row['additional_sales_needed']):,.2f}"
                        ),
                        "Additional Sales %": (
                            f"{float(additional_pct):.2f}%"
                            if additional_pct is not None else "—"
                        ),
                        "Projected Labor Cost": (
                            f"${float(row['projected_labor_cost']):,.2f}"
                        ),
                        "Who Was Scheduled": employees_text
                    })

                st.dataframe(
                    pd.DataFrame(saved_display),
                    use_container_width=True,
                    hide_index=True
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
            salaries_db
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