"""Finding photos by their deployment folder and file name.

Classifier exports (e.g. from TRAPPER) often give a web address as filePath
and a file name whose extension differs from the file on disk (x.JPEG vs
x.jpg), while the photos are stored as <images>/<group>/<deploymentID>/.
"""
from pathlib import Path

import pandas as pd

URL = "https://wildintel-trap.uhu.es/storage/resource/media/4800419/file/"
DEP = "r0025-dona_0011"
NAME = "R0025-DONA_0011__20230509_8232.JPEG"


def _photo(root: Path, *parts: str) -> Path:
    p = root.joinpath(*parts)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"\xff\xd8\xff")
    return p


def test_photo_in_a_deployment_folder_inside_another_folder(tmp_path):
    from camtrap_workflow import MediaResolver
    photo = _photo(tmp_path, "R0025", DEP, "R0025-DONA_0011__20230509_8232.jpg")
    r = MediaResolver(str(tmp_path), tmp_path)
    got = r.resolve(URL, DEP, NAME)
    assert got == photo and r.status(got) == "ok"


def test_photo_directly_in_its_deployment_folder_with_another_extension(tmp_path):
    from camtrap_workflow import MediaResolver
    photo = _photo(tmp_path, DEP, "R0025-DONA_0011__20230509_8232.jpg")
    assert MediaResolver(str(tmp_path), tmp_path).resolve(URL, DEP, NAME) == photo


def test_deployment_folder_name_is_matched_ignoring_case(tmp_path):
    from camtrap_workflow import MediaResolver
    photo = _photo(tmp_path, "R0025", DEP.upper(), "R0025-DONA_0011__20230509_8232.jpg")
    assert MediaResolver(str(tmp_path), tmp_path).resolve(URL, DEP, NAME) == photo


def test_a_photo_found_in_two_deployment_folders_is_not_used(tmp_path):
    from camtrap_workflow import MediaResolver
    _photo(tmp_path, "R0025", DEP, "R0025-DONA_0011__20230509_8232.jpg")
    _photo(tmp_path, "copy", DEP, "R0025-DONA_0011__20230509_8232.jpg")
    r = MediaResolver(str(tmp_path), tmp_path)
    assert r.by_deployment_folder(DEP, NAME) is None
    assert r.status(r.resolve(URL, DEP, NAME)) == "missing"


def test_other_files_with_the_same_stem_are_not_taken_for_the_photo(tmp_path):
    from camtrap_workflow import MediaResolver
    _photo(tmp_path, "R0025", DEP, "R0025-DONA_0011__20230509_8232.json")
    r = MediaResolver(str(tmp_path), tmp_path)
    assert r.by_deployment_folder(DEP, NAME) is None


def test_only_the_needed_levels_are_listed(tmp_path, monkeypatch):
    """images/<group>/<deployment> is found by listing the top two levels,
    never the folders full of photos."""
    import os
    from camtrap_workflow import MediaResolver
    _photo(tmp_path, "R0025", DEP, "R0025-DONA_0011__20230509_8232.jpg")
    _photo(tmp_path, "R0025", "r0025-dona_0012", "x.jpg")
    listed = []
    real = os.scandir

    def spy(path="."):
        listed.append(os.path.relpath(path, tmp_path))
        return real(path)

    monkeypatch.setattr(os, "scandir", spy)
    r = MediaResolver(str(tmp_path), tmp_path)
    assert r.deployment_dirs(DEP)
    assert sorted(listed) == [".", "R0025"]


def test_remote_filepath_shows_the_local_copy_in_the_gallery(tmp_path):
    from camtrap_workflow import MediaResolver, gallery_frame_img_url
    _photo(tmp_path, "R0025", DEP, "R0025-DONA_0011__20230509_8232.jpg")
    r = MediaResolver(str(tmp_path), tmp_path)
    url = gallery_frame_img_url(URL, "4800419", DEP, NAME, str(tmp_path), tmp_path, resolver=r)
    assert url.startswith("/api/image/4800419")


def test_flat_search_ignores_the_extension_case(tmp_path):
    from camtrap_workflow import clear_media_caches, resolve_media_path
    clear_media_caches()
    photo = _photo(tmp_path, "loose", "R0025-DONA_0011__20230509_8232.jpg")
    got = resolve_media_path(URL, "elsewhere", NAME, str(tmp_path), tmp_path, flat_search=True)
    assert got == photo


def test_image_check_finds_trapper_exports(tmp_path):
    """The setup's image check counts these photos as found."""
    from api.routers.fs import check_images
    data = tmp_path / "camtrapdp"
    data.mkdir()
    pd.DataFrame({"mediaID": ["4800419", "4800420"], "deploymentID": [DEP, DEP],
                  "filePath": [URL, URL.replace("4800419", "4800420")],
                  "fileName": [NAME, "R0025-DONA_0011__20230509_9999.JPEG"]}).to_csv(data / "media.csv", index=False)
    images = tmp_path / "images"
    _photo(images, "R0025", DEP, "R0025-DONA_0011__20230509_8232.jpg")
    res = check_images(str(data), str(images))
    assert res["total"] == 2 and res["missing"] == 1
