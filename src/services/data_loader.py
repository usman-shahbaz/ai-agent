
"""Load and validate CSV and Excel datasets."""

from io import BytesIO
from pathlib import Path

import pandas as pd


ALLOWED_EXTENSIONS = {".csv", ".xlsx", ".xls"}
MAX_COLUMNS = 200


class DataLoadError(ValueError):
    """Raised when an uploaded dataset cannot be safely loaded."""


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Ensure every column has a non-empty, unique name."""

    normalized_names = []
    seen = set()

    for index, column in enumerate(df.columns, start=1):
        base_name = str(column).strip()

        if not base_name or base_name.lower() == "nan":
            base_name = f"column_{index}"

        candidate = base_name
        suffix = 2

        while candidate.casefold() in seen:
            candidate = f"{base_name}_{suffix}"
            suffix += 1

        seen.add(candidate.casefold())
        normalized_names.append(candidate)

    df.columns = normalized_names
    return df


def load_dataset(
    uploaded_file,
    max_upload_mb: int = 25,
    max_rows: int = 500_000,
) -> pd.DataFrame:
    """
    Load an uploaded CSV or Excel file into a Pandas DataFrame.

    Args:
        uploaded_file: A file-like object with a name and getvalue().
        max_upload_mb: Maximum permitted file size in megabytes.
        max_rows: Maximum permitted number of data rows.

    Returns:
        A DataFrame with normalized column names.

    Raises:
        DataLoadError: If the file is invalid or exceeds a limit.
    """

    if max_upload_mb < 1 or max_rows < 1:
        raise ValueError("Upload and row limits must be positive.")

    filename = Path(
        str(getattr(uploaded_file, "name", ""))
    ).name

    extension = Path(filename).suffix.lower()

    if extension not in ALLOWED_EXTENSIONS:
        raise DataLoadError(
            "Unsupported file type. Please upload CSV, XLSX, or XLS."
        )

    try:
        raw_data = uploaded_file.getvalue()
    except Exception as exc:
        raise DataLoadError(
            "Unable to read the uploaded file."
        ) from exc

    if not raw_data:
        raise DataLoadError("The uploaded file is empty.")

    max_bytes = max_upload_mb * 1024 * 1024

    if len(raw_data) > max_bytes:
        raise DataLoadError(
            f"File exceeds the {max_upload_mb} MB limit."
        )

    try:
        file_buffer = BytesIO(raw_data)

        if extension == ".csv":
            # Read one extra row so we can detect an exceeded row limit.
            try:
                df = pd.read_csv(
                    file_buffer,
                    encoding="utf-8-sig",
                    nrows=max_rows + 1,
                    low_memory=False,
                    on_bad_lines="error",
                )
            except UnicodeDecodeError:
                file_buffer.seek(0)
                df = pd.read_csv(
                    file_buffer,
                    encoding="latin-1",
                    nrows=max_rows + 1,
                    low_memory=False,
                    on_bad_lines="error",
                )
        else:
            engine = "openpyxl" if extension == ".xlsx" else "xlrd"

            df = pd.read_excel(
                file_buffer,
                engine=engine,
                nrows=max_rows + 1,
            )

    except Exception as exc:
        raise DataLoadError(
            "Could not parse the uploaded file. "
            "Check its format, headers, and contents."
        ) from exc

    if len(df) > max_rows:
        raise DataLoadError(
            f"The dataset exceeds the limit of "
            f"{max_rows:,} data rows."
        )

    if len(df.columns) == 0:
        raise DataLoadError(
            "No columns were found in the uploaded file."
        )

    if len(df.columns) > MAX_COLUMNS:
        raise DataLoadError(
            f"The file contains too many columns. "
            f"The maximum is {MAX_COLUMNS}."
        )

    df = _normalize_columns(df)

    if df.empty:
        raise DataLoadError(
            "The file contains column headers but no data rows."
        )

    # Avoid returning a view tied to another DataFrame.
    return df.reset_index(drop=True)
