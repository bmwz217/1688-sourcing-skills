"""Create Q90 JPEG references without resizing or overwriting source images."""

import argparse
import hashlib
from io import BytesIO
from pathlib import Path

from PIL import Image
from pydantic import BaseModel, Field


class CompressionRequest(BaseModel):
    """Validated paths for a batch of reference images."""

    output_dir: Path
    sources: list[Path] = Field(min_length=1)


class CompressedReference(BaseModel):
    """Source mapping and upload size of one compressed image."""

    source: Path
    path: Path
    bytes: int
    width: int
    height: int


class CompressionResult(BaseModel):
    """Machine-readable reference paths for the input ledger."""

    images: list[CompressedReference]


def compress_references(request: CompressionRequest) -> CompressionResult:
    """Convert each source to RGB JPEG at quality 90, flattening alpha to white."""
    sources = [source.resolve(strict=True) for source in request.sources]
    output_dir = request.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for source in sources:
        data = source.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        destination = output_dir / f"{source.stem}_{digest}_q90.jpg"
        if destination in sources:
            raise ValueError(f"Output would overwrite a source image: {destination}")
        with Image.open(BytesIO(data)) as original:
            if getattr(original, "n_frames", 1) != 1:
                raise ValueError(f"Use a single-frame reference image: {source}")
            rgba = original.convert("RGBA")
            rgb = Image.new("RGB", rgba.size, "white")
            rgb.paste(rgba, mask=rgba.getchannel("A"))
            options = {"quality": 90}
            if original.info.get("exif"):
                options["exif"] = original.info["exif"]
            if original.mode in ("RGB", "RGBA") and original.info.get("icc_profile"):
                options["icc_profile"] = original.info["icc_profile"]
            encoded = BytesIO()
            rgb.save(encoded, format="JPEG", **options)
            payload = encoded.getvalue()
            if destination.exists():
                if destination.read_bytes() != payload:
                    raise ValueError(f"Existing output differs; use a new output directory: {destination}")
            else:
                with destination.open("xb") as target:
                    target.write(payload)
            results.append(
                CompressedReference(
                    source=source,
                    path=destination,
                    bytes=len(payload),
                    width=rgb.width,
                    height=rgb.height,
                )
            )
    return CompressionResult(images=results)


def main() -> None:
    """Compress CLI inputs and print a JSON result for reference registration."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("sources", nargs="+", type=Path)
    args = parser.parse_args()
    request = CompressionRequest(output_dir=args.output_dir, sources=args.sources)
    print(compress_references(request).model_dump_json(indent=2))


if __name__ == "__main__":
    main()
