

from app.core.config import settings


ALLOWED_EXTENSIONS = {
    ".csv",
    ".xlsx",
    ".parquet",
}


class DatasetService:

    def __init__(self):

        self.upload_dir = Path(
            settings.storage_path
        ) / "uploads"

        self.processed_dir = Path(
            settings.storage_path
        ) / "processed"

        self.upload_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        self.processed_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

    def save_dataset(self, file):

        extension = Path(
            file.filename
        ).suffix.lower()

        if extension not in ALLOWED_EXTENSIONS:
            raise ValueError(
                "Unsupported file format."
            )

        dataset_id = str(uuid.uuid4())

        original_path = (
            self.upload_dir
            / f"{dataset_id}{extension}"
        )

        with open(original_path, "wb") as output:

            while chunk := file.file.read(1024 * 1024):

                output.write(chunk)

        processed_path = self._convert_to_parquet(
            original_path,
            dataset_id,
        )

        return {
            "dataset_id": dataset_id,
            "path": str(processed_path),
        }

    def _convert_to_parquet(
        self,
        path: Path,
        dataset_id: str,
    ):

        if path.suffix == ".csv":

            df = pd.read_csv(path)

        elif path.suffix == ".xlsx":

            df = pd.read_excel(path)

        elif path.suffix == ".parquet":

            df = pd.read_parquet(path)

        else:

            raise ValueError(
                "Unsupported file type."
            )

        if len(df) > settings.max_rows:

            raise ValueError(
                f"Dataset exceeds "
                f"{settings.max_rows:,} rows."
            )

        output_path = (
            self.processed_dir
            / f"{dataset_id}.parquet"
        )

        df.to_parquet(
            output_path,
            index=False,
        )

        return output_path
