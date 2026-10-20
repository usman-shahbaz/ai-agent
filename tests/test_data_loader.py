
"""Tests for CSV and Excel data loading."""

from io import BytesIO

import pandas as pd
import pytest

from src.services.data_loader import DataLoadError, load_dataset


class FakeUploadedFile:
    """Mimic the relevant parts of a Streamlit uploaded file."""

    def __init__(self, name: str, content: bytes) -> None:
        self.name = name
        self._content = content

    def getvalue(self) -> bytes:
        return self._content


def make_csv(
    name: str = "data.csv",
    content: bytes = b"name,age\nAli,25\nSara,30\n",
) -> FakeUploadedFile:
    return FakeUploadedFile(name, content)


def test_loads_valid_csv() -> None:
    uploaded = make_csv()

    df = load_dataset(uploaded)

    assert list(df.columns) == ["name", "age"]
    assert len(df) == 2
    assert df.iloc[0]["name"] == "Ali"


def test_normalizes_duplicate_column_names() -> None:
    uploaded = make_csv(
        content=b"name,name\nAli,25\nSara,30\n"
    )

    df = load_dataset(uploaded)

    assert list(df.columns) == ["name", "name_2"]


def test_rejects_unsupported_file_type() -> None:
    uploaded = FakeUploadedFile("data.json", b'{"name": "Ali"}')

    with pytest.raises(DataLoadError, match="Unsupported file type"):
        load_dataset(uploaded)


def test_rejects_empty_file() -> None:
    uploaded = make_csv(content=b"")

    with pytest.raises(DataLoadError, match="empty"):
        load_dataset(uploaded)


def test_rejects_file_over_size_limit() -> None:
    uploaded = make_csv(content=b"name\n" + b"A" * 100)

    with pytest.raises(DataLoadError, match="exceeds"):
        load_dataset(uploaded, max_upload_mb=1) if len(
            uploaded.getvalue()
        ) > 1024 * 1024 else load_dataset(
            FakeUploadedFile(
                "data.csv",
                b"name\n" + b"A" * (1024 * 1024 + 1),
            ),
            max_upload_mb=1,
        )


def test_rejects_more_rows_than_limit() -> None:
    uploaded = make_csv(
        content=b"name\nAli\nSara\nUsman\n"
    )

    with pytest.raises(DataLoadError, match="exceeds the limit"):
        load_dataset(uploaded, max_rows=2)


def test_rejects_file_with_no_data_rows() -> None:
    uploaded = make_csv(content=b"name,age\n")

    with pytest.raises(DataLoadError, match="no data rows"):
        load_dataset(uploaded)


def test_loads_valid_xlsx() -> None:
    buffer = BytesIO()
    pd.DataFrame(
        {"name": ["Ali", "Sara"], "age": [25, 30]}
    ).to_excel(buffer, index=False, engine="openpyxl")

    uploaded = FakeUploadedFile("data.xlsx", buffer.getvalue())
    df = load_dataset(uploaded)

    assert len(df) == 2
    assert list(df.columns) == ["name", "age"]
