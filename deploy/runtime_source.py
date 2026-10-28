import argparse
import hashlib
import io
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

FILES = {
    "Dockerfile": "Dockerfile.decider-test",
    "runtime_downloads.py": "runtime_downloads.py",
    "buildspec.yml": "buildspec.runtime.yml",
}


def build_source(directory: Path) -> bytes:
    buffer = io.BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as archive:
        for name, source in FILES.items():
            info = ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, (directory / source).read_bytes())
    return buffer.getvalue()


def main() -> None:
    parser = argparse.ArgumentParser(description="Package only reviewed runtime build inputs.")
    parser.add_argument("output", type=Path)
    arguments = parser.parse_args()
    source = build_source(Path(__file__).resolve().parent)
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_bytes(source)
    print(hashlib.sha256(source).hexdigest())


if __name__ == "__main__":
    main()
