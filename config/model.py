from dataclasses import dataclass
from pathlib import Path

import pandas as pd


@dataclass
class Dataset:
    name: str
    file_path: Path
    dataframe: pd.DataFrame
    columns: list[str]

    @property
    def row_count(self) -> int:
        return len(self.dataframe)

    @property
    def column_count(self) -> int:
        return len(self.columns)
