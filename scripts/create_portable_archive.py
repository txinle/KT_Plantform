import argparse
import tarfile
from pathlib import Path


def add_tree(source: Path, output: Path) -> None:
    paths = sorted(
        source.rglob("*"),
        key=lambda path: (len(path.relative_to(source).parts), path.as_posix()),
    )
    with tarfile.open(output, "w:gz") as archive:
        for path in paths:
            relative = path.relative_to(source).as_posix()
            info = archive.gettarinfo(str(path), arcname=relative)
            info.uid = 0
            info.gid = 0
            info.uname = "root"
            info.gname = "root"
            info.mtime = 0
            if path.is_dir():
                info.mode = 0o755
                archive.addfile(info)
            else:
                info.mode = 0o755 if relative == "run_local.sh" else 0o644
                with path.open("rb") as source_file:
                    archive.addfile(info, source_file)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    add_tree(args.source.resolve(), args.output.resolve())


if __name__ == "__main__":
    main()
