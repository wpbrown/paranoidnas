from isomodder import (
    UbuntuServerIsoFetcher,
    IsoFile,
    AutoInstallBuilder
)
from ._ui import get_rich
from pathlib import Path
import enum
from ruamel import yaml
import pkgutil
from typing import Any, Collection
from ruamel.yaml.comments import CommentedSeq, CommentedMap
import logging
import tarfile
import io
import contextlib
import tempfile
import stat


class BootMode(enum.Enum):
    MBR = enum.auto()
    EFI = enum.auto()


def _convert_to_mbr_storage(data: Any) -> None:
    config: CommentedSeq = data["storage"]["config"]
    first_part = next(c for c in config if c["type"] == "partition")
    first_part["id"] = "grub_partition"
    first_part["size"] = "1MB"
    first_part["flag"] = "bios_grub"
    del first_part["grub_device"]

    root_part = next(c for c in config if c["id"] == "root_partition")
    root_part["flag"] = "boot"

    efi_configs = [c for c in config if c["id"].startswith("efi")]
    for c in efi_configs:
        config.remove(c)


def create_paranoidnas_autoinstall_yaml(
    boot_mode: BootMode, username: str, hostname: str, locale: str, kb_layout: str, authorized_keys: Collection[str]
) -> str:
    template_text = pkgutil.get_data(__name__, "user-data.yaml").decode()
    document = yaml.load(template_text, Loader=yaml.RoundTripLoader, preserve_quotes=True)
    data: CommentedMap = document["autoinstall"]

    if boot_mode == BootMode.MBR:
        _convert_to_mbr_storage(data)
    
    data['identity']['username'] = username
    data['identity']['hostname'] = hostname
    data['locale'] = locale
    data['keyboard']['layout'] = kb_layout

    ssh_keys = data['ssh']['authorized-keys']
    if len(authorized_keys) > 0:
        ssh_keys.clear()
        ssh_keys.extend(authorized_keys)
    else:
        del data['ssh']['authorized-keys']

    return yaml.dump(document, Dumper=yaml.RoundTripDumper, width=4096)


def create_paranoidnas_iso(working_dir: Path, boot_mode: BootMode, autoinstall_yaml: str, autoinstall_prompt: bool) -> None:
    fetcher = UbuntuServerIsoFetcher(working_dir=working_dir, release="20.04")
    with get_rich() as progress:
        iso_path = fetcher.fetch(progress)
    iso_file = IsoFile(iso_path)
    builder = AutoInstallBuilder(
        source_iso=iso_file,
        autoinstall_yaml=autoinstall_yaml,
        grub_entry_stamp="paranoidNAS AutoInstall",
        autoinstall_prompt=autoinstall_prompt,
        supports_efi=(boot_mode == BootMode.EFI),
        supports_mbr=(boot_mode == BootMode.MBR),
    )
    
    _copy_media_content_to_iso(iso_file)
    builder.build()
    target_path = working_dir / "paranoidNAS.iso"
    if target_path.exists():
        target_path.unlink()
    with get_rich() as progress:
        iso_file.write_iso(target_path, progress)


def _copy_media_content_to_iso(iso_file: IsoFile) -> None:
    dest_path = Path("/paranoid")

    test_path = Path(__file__).parent / "media_content"
    if test_path.is_dir():
        logging.debug(f"Using local filesystem media_content at '{test_path}'.")
        iso_file.copy_directory(test_path, dest_path)
        return

    try:
        tar_data = pkgutil.get_data(__name__, "media_content.tar")
    except OSError:
        tar_data = None

    if tar_data is None:
        raise Exception("Broken packaging. Missing media_content directory or tar file.")

    logging.debug("Using media_content.tar from abstract package resource.")
    tar_file = tarfile.open(fileobj=io.BytesIO(tar_data), mode="r")
    _unpack_tar_to_iso(tar_file, iso_file)


def _unpack_tar_to_iso(tar_file: tarfile.TarFile, iso_file: IsoFile) -> None:
    target_path = Path("/paranoid")
    iso_file.create_directory(target_path)
    infos = (info for info in iter(tar_file.next, None) if info.path != '.')
    for info in infos:
        path = target_path / info.path
        if info.isdir():
            iso_file.create_directory(path)
        else:
            fp = tar_file.extractfile(info)
            file_mode = 0o0100555 if info.mode & stat.S_IXUSR else None
            iso_file.write_fp(path, fp, length=info.size, file_mode=file_mode)


