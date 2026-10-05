"""Bundle ingestion: archive extraction and band discovery for Sentinel-2 and Landsat 8/9.

Each sensor distributes a scene as a collection of single-band files. This module
extracts the archive, identifies each band file, and maps band IDs to semantic roles.
"""
from __future__ import annotations

import re
import shutil
import tarfile
import uuid
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

# Maximum allowed uncompressed size per archive member (20 GiB).
_MAX_MEMBER_BYTES = 20 * 1024 ** 3

SENTINEL2_BAND_ROLES: dict[str, str] = {
    "B01": "coastal",
    "B02": "blue",
    "B03": "green",
    "B04": "red",
    "B05": "rededge1",
    "B06": "rededge2",
    "B07": "rededge3",
    "B08": "nir",
    "B8A": "nir_narrow",
    "B09": "water_vapor",
    "B11": "swir1",
    "B12": "swir2",
}

# LS8 and LS9 share the same band-to-role mapping (OLI/TIRS layout is identical).
LANDSAT_BAND_ROLES: dict[str, str] = {
    "B1": "coastal",
    "B2": "blue",
    "B3": "green",
    "B4": "red",
    "B5": "nir",
    "B6": "swir1",
    "B7": "swir2",
    "B8": "pan",
    "B10": "thermal",
    "B11": "thermal2",
}

VALID_ROLES: frozenset[str] = frozenset(
    list(SENTINEL2_BAND_ROLES.values()) + list(LANDSAT_BAND_ROLES.values()) + ["other"]
)


@dataclass
class BandFile:
    path: Path
    band_id: str          # normalised upper-case: "B02", "B8A", "B4"
    resolution_m: Optional[int]   # 10/20/60 for S2; None for Landsat
    auto_role: Optional[str]      # looked up from band role tables; None if unknown


def detect_bundle_type(path: Path) -> Literal["sentinel2", "landsat"]:
    name = path.name.lower()
    if name.endswith(".zip"):
        return "sentinel2"
    if name.endswith(".tar.gz") or name.endswith(".tgz"):
        return "landsat"
    raise ValueError(f"Unsupported bundle format: {path.name}. Expected .zip (Sentinel-2) or .tar.gz (Landsat).")


def extract_bundle(archive_path: Path, staging_dir: Path) -> Path:
    """Extract archive into staging_dir and return the top-level extracted path.

    Raises ValueError on corrupt archives or path-traversal attempts.
    """
    staging_dir.mkdir(parents=True, exist_ok=True)
    name = archive_path.name.lower()

    if name.endswith(".zip"):
        return _extract_zip(archive_path, staging_dir)
    else:
        return _extract_tar(archive_path, staging_dir)


def _extract_zip(archive_path: Path, staging_dir: Path) -> Path:
    try:
        with zipfile.ZipFile(archive_path) as zf:
            for info in zf.infolist():
                member_path = (staging_dir / info.filename).resolve()
                if not str(member_path).startswith(str(staging_dir.resolve())):
                    raise ValueError(f"Path traversal detected in archive member: {info.filename}")
                if info.file_size > _MAX_MEMBER_BYTES:
                    raise ValueError(f"Archive member too large ({info.filename}): {info.file_size} bytes")
            zf.extractall(staging_dir)
    except zipfile.BadZipFile as exc:
        raise ValueError(f"Corrupt .zip archive: {exc}") from exc

    return _find_extracted_root(staging_dir)


def _extract_tar(archive_path: Path, staging_dir: Path) -> Path:
    try:
        with tarfile.open(archive_path, "r:gz") as tf:
            for member in tf.getmembers():
                member_path = (staging_dir / member.name).resolve()
                if not str(member_path).startswith(str(staging_dir.resolve())):
                    raise ValueError(f"Path traversal detected in archive member: {member.name}")
                if member.size > _MAX_MEMBER_BYTES:
                    raise ValueError(f"Archive member too large ({member.name}): {member.size} bytes")
            tf.extractall(staging_dir)
    except tarfile.TarError as exc:
        raise ValueError(f"Corrupt .tar.gz archive: {exc}") from exc

    return _find_extracted_root(staging_dir)


def _find_extracted_root(staging_dir: Path) -> Path:
    """Return the single top-level entry if archive unpacks to a directory, else staging_dir."""
    children = [p for p in staging_dir.iterdir()]
    if len(children) == 1 and children[0].is_dir():
        return children[0]
    return staging_dir


# ---------------------------------------------------------------------------
# Sentinel-2
# ---------------------------------------------------------------------------

_S2_BAND_RE = re.compile(r"_B(8A|\d{2})(?:_|\.)", re.IGNORECASE)
_S2_RES_RE = re.compile(r"R(\d+)m", re.IGNORECASE)


def discover_sentinel2_bands(safe_root: Path) -> list[BandFile]:
    """Find all JP2 band files inside a SAFE directory."""
    bands: list[BandFile] = []

    # Tolerate both the .SAFE directory wrapper and the raw GRANULE/ layout.
    safe_dir = safe_root
    if not (safe_root / "GRANULE").exists():
        # Archive may have extracted with the .SAFE directory as a direct child.
        candidates = list(safe_root.glob("*.SAFE"))
        if candidates:
            safe_dir = candidates[0]

    img_dirs = list(safe_dir.glob("GRANULE/*/IMG_DATA/R*m"))
    if not img_dirs:
        # Some L1C scenes lack the R{res}m subdirectory layer.
        img_dirs = list(safe_dir.glob("GRANULE/*/IMG_DATA"))

    for img_dir in img_dirs:
        res_m: Optional[int] = None
        res_match = _S2_RES_RE.search(img_dir.name)
        if res_match:
            res_m = int(res_match.group(1))

        for jp2 in img_dir.glob("*.jp2"):
            m = _S2_BAND_RE.search(jp2.name)
            if not m:
                continue
            band_id = "B" + m.group(1).upper()
            role = SENTINEL2_BAND_ROLES.get(band_id)
            bands.append(BandFile(path=jp2, band_id=band_id, resolution_m=res_m, auto_role=role))

    # Deduplicate: keep the highest-resolution copy of each band_id.
    seen: dict[str, BandFile] = {}
    for bf in bands:
        if bf.band_id not in seen:
            seen[bf.band_id] = bf
        else:
            existing = seen[bf.band_id]
            if bf.resolution_m is not None and (
                existing.resolution_m is None or bf.resolution_m < existing.resolution_m
            ):
                seen[bf.band_id] = bf

    return sorted(seen.values(), key=lambda b: b.band_id)


def parse_sentinel2_scene_id(safe_root: Path) -> str:
    """Derive a stable scene ID from the .SAFE directory name or MTD filename."""
    # e.g. S2A_MSIL2A_20240315T103200_N0509_R108_T30UXD_20240315T152030.SAFE
    safe_name = safe_root.name
    if safe_name.endswith(".SAFE"):
        safe_name = safe_name[:-5]

    # Try to extract tile + date: T{tile}_{date}
    m = re.search(r"_(T\w{5})_(\d{8}T\d{6})", safe_name)
    if m:
        tile, dt = m.group(1), m.group(2)
        prefix = safe_name.split("_")[0]  # "S2A" or "S2B"
        return f"{prefix}_{tile}_{dt[:8]}"

    # Fallback: use the full name (truncated for safety).
    return re.sub(r"[^\w]", "_", safe_name)[:64]


# ---------------------------------------------------------------------------
# Landsat 8/9
# ---------------------------------------------------------------------------

_LS_BAND_RE = re.compile(r"_B(\d+)\.TIF$", re.IGNORECASE)
_LS_SCENE_RE = re.compile(r"^(LC0[89]_L\w+_\d{6}_\d{8})", re.IGNORECASE)


def discover_landsat_bands(extracted_root: Path) -> list[BandFile]:
    """Find all TIF band files in a Landsat scene directory."""
    bands: list[BandFile] = []
    # Landsat files are at the top level (no subdirectory).
    search_dir = extracted_root
    tifs = list(search_dir.glob("*.TIF")) + list(search_dir.glob("*.tif"))

    for tif in tifs:
        m = _LS_BAND_RE.search(tif.name)
        if not m:
            continue
        band_id = "B" + m.group(1)
        role = LANDSAT_BAND_ROLES.get(band_id)
        if role is None:
            continue  # skip QA/aerosol bands not in the role table
        bands.append(BandFile(path=tif, band_id=band_id, resolution_m=30, auto_role=role))

    return sorted(bands, key=lambda b: b.band_id)


def parse_landsat_scene_id(extracted_root: Path) -> str:
    """Derive scene ID from the first LC0[89]_* filename found."""
    for f in extracted_root.iterdir():
        m = _LS_SCENE_RE.match(f.name)
        if m:
            return m.group(1)
    # Fallback: directory name.
    return re.sub(r"[^\w]", "_", extracted_root.name)[:64]


def detect_landsat_sensor(extracted_root: Path) -> str:
    """Return 'landsat8' or 'landsat9' based on filename prefix."""
    for f in extracted_root.iterdir():
        if f.name.upper().startswith("LC08"):
            return "landsat8"
        if f.name.upper().startswith("LC09"):
            return "landsat9"
    return "landsat"


# ---------------------------------------------------------------------------
# Acquisition time parsing
# ---------------------------------------------------------------------------

def parse_acquisition_time(root: Path, sensor: str) -> Optional[str]:
    """Extract ISO8601 UTC acquisition time from scene metadata. Returns None on failure."""
    try:
        if sensor == "sentinel2":
            return _parse_s2_time(root)
        else:
            return _parse_landsat_time(root)
    except Exception:
        return None


def _parse_s2_time(root: Path) -> Optional[str]:
    # Try MTD_MSIL2A.xml at the .SAFE root; also check one level up.
    for search in (root, *root.parent.glob("*.SAFE")):
        mtd = search / "MTD_MSIL2A.xml"
        if mtd.exists():
            tree = ET.parse(mtd)
            ns = {"n1": "https://psd-14.sentinel2.eo.esa.int/PSD/User_Product_Level-2A.xsd"}
            # SENSING_TIME is in General_Info/Product_Info
            for elem in tree.iter():
                if elem.tag.endswith("SENSING_TIME") and elem.text:
                    # Already ISO8601 UTC: "2024-03-15T10:32:00.000000Z"
                    return elem.text.strip()
    return None


def _parse_landsat_time(root: Path) -> Optional[str]:
    mtl_files = list(root.glob("*_MTL.txt"))
    if not mtl_files:
        return None
    date_str = time_str = None
    with mtl_files[0].open() as f:
        for line in f:
            line = line.strip()
            if line.startswith("DATE_ACQUIRED"):
                date_str = line.split("=")[-1].strip()
            elif line.startswith("SCENE_CENTER_TIME"):
                time_str = line.split("=")[-1].strip().strip('"').split(".")[0]
    if date_str and time_str:
        return f"{date_str}T{time_str}Z"
    return None


# ---------------------------------------------------------------------------
# Unique staging helper
# ---------------------------------------------------------------------------

def make_staging_path(base: Path) -> Path:
    """Return a unique sub-directory under base for one upload session."""
    return base / uuid.uuid4().hex[:12]
