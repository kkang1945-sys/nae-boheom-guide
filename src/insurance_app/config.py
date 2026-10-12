from dataclasses import dataclass
import os
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    project_root: Path
    data_dir: Path
    upload_dir: Path
    database_path: Path
    max_upload_bytes: int = 25 * 1024 * 1024
    external_ai_enabled: bool = False
    tesseract_path: Path | None = None
    tessdata_dir: Path | None = None
    hira_disease_api_key: str | None = None
    public_demo: bool = False


def load_settings(project_root: Path | None = None) -> Settings:
    root = (project_root or Path.cwd()).resolve()
    data_dir = root / "data"
    return Settings(
        project_root=root,
        data_dir=data_dir,
        upload_dir=data_dir / "uploads",
        database_path=data_dir / "insurance_app.sqlite3",
        tesseract_path=(Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
                        if Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe").is_file() else None),
        tessdata_dir=(root / "tools" / "tessdata" if (root / "tools" / "tessdata" / "kor.traineddata").is_file() else None),
        hira_disease_api_key=os.environ.get("HIRA_DISEASE_API_KEY") or None,
        public_demo=os.environ.get("PUBLIC_DEMO", "").strip().lower() in {"1", "true", "yes", "on"},
    )
