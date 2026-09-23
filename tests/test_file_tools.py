"""File-moving tools: 01 organizer, 02 duplicates, 03 rename, 04 backup, 09 disk report, 10 cleaner."""

from __future__ import annotations

import hashlib
import json
import os
import struct
import time
import zipfile
from pathlib import Path

import pytest

from conftest import load_script

DAY = 86400


def write(path: Path, text: str = "x", age_days: float | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if age_days is not None:
        stamp = time.time() - age_days * DAY
        os.utime(path, (stamp, stamp))
    return path


def tree(root: Path) -> dict[str, str]:
    """{relative posix path: sha256} for every file under root."""
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


def dirs(root: Path) -> set[str]:
    return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_dir()}


# --------------------------------------------------------------------------- 01 organizer

def test_organizer_dry_run_moves_nothing(tmp_path, run):
    write(tmp_path / "in" / "a.pdf")
    write(tmp_path / "in" / "b.png")
    before = tree(tmp_path / "in")
    result = run("01", tmp_path / "in")
    assert result.returncode == 0
    assert "Dry-run only" in result.stdout
    assert tree(tmp_path / "in") == before


def test_organizer_apply_undo_restores_and_prunes_empty_folders(tmp_path, run):
    folder = tmp_path / "in"
    write(folder / "invoice.pdf", "pdf")
    write(folder / "shot.png", "png")
    write(folder / "desktop.ini", "[.ShellClassInfo]")
    write(folder / "movie.mkv.crdownload", "partial")
    before = tree(folder)

    applied = run("01", folder, "--by", "type-date", "--apply")
    assert applied.returncode == 0, applied.stderr
    assert (folder / "desktop.ini").exists(), "folder metadata must never move"
    assert (folder / "movie.mkv.crdownload").exists(), "partial downloads must never move"
    assert not (folder / "invoice.pdf").exists()
    log = next(folder.glob("undo_*.json"))

    undone = run("01", "--undo", log)
    assert undone.returncode == 0, undone.stderr
    after = tree(folder)
    after.pop(log.name)
    assert after == before
    assert dirs(folder) == set(), "undo must remove the category/date folders it emptied"


def test_organizer_undo_keeps_folders_that_hold_other_files(tmp_path, run):
    folder = tmp_path / "in"
    write(folder / "Images" / "already_here.png", "mine")
    write(folder / "new.png", "new")
    run("01", folder, "--apply")
    log = next(folder.glob("undo_*.json"))
    run("01", "--undo", log)
    assert (folder / "new.png").read_text() == "new"
    assert (folder / "Images" / "already_here.png").read_text() == "mine"


# --------------------------------------------------------------------------- 02 duplicates

@pytest.fixture
def dup_tree(tmp_path):
    root = tmp_path / "pics"
    old = write(root / "2019" / "beach.jpg", "SAME-CONTENT", age_days=400)
    mid = write(root / "b.jpg", "SAME-CONTENT", age_days=100)
    new = write(root / "backup" / "deep" / "beach copy.jpg", "SAME-CONTENT", age_days=1)
    write(root / "unique.jpg", "different")
    write(root / ".git" / "objects" / "ab" / "cdef", "SAME-CONTENT")  # must never be touched
    return root, old, mid, new


def test_duplicates_dry_run_reports_and_moves_nothing(dup_tree, run):
    root, old, mid, new = dup_tree
    before = tree(root)
    result = run("02", root, "--dry-run")
    assert result.returncode == 0, result.stderr
    assert "Group 1: 3 copies" in result.stdout
    assert "Dry-run: nothing moved" in result.stdout
    assert f"keep  {new}" in result.stdout  # default policy: newest
    assert tree(root) == before
    assert not list(root.glob("_duplicates_*"))


@pytest.mark.parametrize("policy, keeper", [("newest", 3), ("oldest", 1), ("shortest-path", 2)])
def test_duplicates_keep_policy_json(dup_tree, run, policy, keeper):
    root, *files = dup_tree
    expected_keep = files[keeper - 1]
    result = run("02", root, "--keep", policy, "--json")
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["policy"] == policy
    assert report["moved"] == 2
    assert report["groups"][0]["keep"] == str(expected_keep)
    assert expected_keep.exists()
    assert sum(f.exists() for f in files) == 1
    assert (root / ".git" / "objects" / "ab" / "cdef").exists()


def test_duplicates_undo_restores_every_file_bit_for_bit(dup_tree, run):
    root, *_ = dup_tree
    before = tree(root)
    moved = run("02", root, "--keep", "newest")
    assert moved.returncode == 0, moved.stderr
    quarantine = next(root.glob("_duplicates_*"))
    assert (quarantine / "2019" / "beach.jpg").exists(), "quarantine mirrors the original layout"

    undone = run("02", "--undo", quarantine / "manifest.json")
    assert undone.returncode == 0, undone.stderr
    assert tree(root) == before
    assert not quarantine.exists(), "the emptied quarantine folder is removed"


def test_duplicates_undo_reports_conflicts_and_keeps_them(dup_tree, run):
    root, old, mid, new = dup_tree
    run("02", root, "--keep", "newest")
    manifest = next(root.glob("_duplicates_*")) / "manifest.json"
    write(old, "someone recreated this file")
    result = run("02", "--undo", manifest)
    assert "conflict" in result.stdout
    assert old.read_text() == "someone recreated this file"
    assert mid.read_text() == "SAME-CONTENT"
    remaining = json.loads(manifest.read_text())
    assert [e["original"] for e in remaining] == [str(old)]


def test_duplicates_without_terminal_or_policy_explains_instead_of_crashing(dup_tree, run):
    root, *_ = dup_tree
    before = tree(root)
    result = run("02", root, stdin="")
    assert result.returncode == 1
    assert "EOFError" not in result.stderr
    assert "--keep newest|oldest|shortest-path" in result.stderr
    assert tree(root) == before


def test_duplicates_ignores_hard_links(tmp_path):
    mod = load_script("02")
    root = tmp_path / "t"
    original = write(root / "a.bin", "payload")
    try:
        os.link(original, root / "b.bin")
    except OSError:
        pytest.skip("filesystem without hard links")
    assert mod.collect_groups(root, 1, quiet=True) == []


def test_duplicates_legacy_auto_keep_newest_still_works(dup_tree, run):
    root, old, mid, new = dup_tree
    result = run("02", root, "--auto-keep-newest")
    assert result.returncode == 0, result.stderr
    assert new.exists() and not old.exists() and not mid.exists()


# --------------------------------------------------------------------------- 03 rename

def test_rename_chained_targets_apply_and_undo(tmp_path, run):
    """Audit repro: '_'->zz_1, 'a'->zz_2 while zz_2->zz_3 crashed or overwrote a file."""
    folder = tmp_path / "ren"
    write(folder / "_.txt", "underscore")
    write(folder / "a.txt", "A")
    write(folder / "zz_2.txt", "Z")
    result = run("03", folder, "--find", "^(_|a|zz_2)$", "--replace", "zz_{n}", "--pad", "1", "--apply")
    assert result.returncode == 0, result.stderr
    assert {p.name: p.read_text() for p in folder.glob("zz_*.txt")} == {
        "zz_1.txt": "underscore", "zz_2.txt": "A", "zz_3.txt": "Z"}
    log = next(folder.glob("rename_undo_*.json"))
    assert json.loads(log.read_text())["status"] == "done"

    undone = run("03", "--undo", log)
    assert undone.returncode == 0, undone.stderr
    assert (folder / "_.txt").read_text() == "underscore"
    assert (folder / "a.txt").read_text() == "A"
    assert (folder / "zz_2.txt").read_text() == "Z"
    assert not (folder / "zz_1.txt").exists() and not (folder / "zz_3.txt").exists()


def test_rename_bad_template_is_a_clean_error(tmp_path, run):
    write(tmp_path / "d" / "a.txt")
    result = run("03", tmp_path / "d", "--find", "^a$", "--replace", r"\2")
    assert result.returncode == 1
    assert "bad --replace template" in result.stderr
    assert "Traceback" not in result.stderr


def test_rename_refuses_real_collisions(tmp_path, run):
    folder = tmp_path / "d"
    write(folder / "a.txt", "A")
    write(folder / "b.txt", "B")
    result = run("03", folder, "--find", "^.*$", "--replace", "same", "--apply")
    assert result.returncode == 1
    assert "COLLISION" in result.stderr
    assert (folder / "a.txt").read_text() == "A" and (folder / "b.txt").read_text() == "B"


def test_rename_failure_midway_rolls_everything_back(tmp_path, monkeypatch):
    mod = load_script("03")
    folder = tmp_path / "d"
    files = [write(folder / f"{n}.txt", n) for n in ("a", "b", "c")]
    entries = mod.stage([(f, f.with_name(f"new_{f.name}")) for f in files])
    moves = [(Path(e["from"]), Path(e["tmp"]), Path(e["to"])) for e in entries]

    real_rename = Path.rename
    calls = {"n": 0}

    def flaky_rename(self, target):
        calls["n"] += 1
        if calls["n"] == 5:  # 3 staging renames succeed, then the 2nd final rename fails
            raise PermissionError(13, "file is locked", str(self))
        return real_rename(self, target)

    monkeypatch.setattr(Path, "rename", flaky_rename)
    with pytest.raises(mod.RenameError) as info:
        mod.two_phase_rename(moves)
    monkeypatch.setattr(Path, "rename", real_rename)
    assert info.value.restored
    assert sorted(p.name for p in folder.iterdir()) == ["a.txt", "b.txt", "c.txt"]
    assert [f.read_text() for f in files] == ["a", "b", "c"]


def test_rename_undo_recovers_a_run_killed_between_phases(tmp_path):
    mod = load_script("03")
    folder = tmp_path / "d"
    files = [write(folder / f"{n}.txt", n) for n in ("a", "b")]
    entries = mod.stage([(f, f.with_name(f"z{f.name}")) for f in files])
    log = folder / "rename_undo_20260101-000000.json"
    mod.write_log(log, entries, "in-progress")
    for e in entries:                     # phase 1 happened, then the process died
        Path(e["from"]).rename(e["tmp"])
    assert mod.undo(log) == 2
    assert [f.read_text() for f in files] == ["a", "b"]


def test_rename_undo_accepts_v1_logs(tmp_path):
    mod = load_script("03")
    folder = tmp_path / "d"
    write(folder / "new.txt", "content")
    log = folder / "rename_undo_old.json"
    log.write_text(json.dumps([{"from": str(folder / "old.txt"), "to": str(folder / "new.txt")}]))
    assert mod.undo(log) == 1
    assert (folder / "old.txt").read_text() == "content"


def test_rename_never_renames_its_own_undo_logs(tmp_path, run):
    folder = tmp_path / "d"
    write(folder / "a.txt")
    run("03", folder, "--find", "^.*$", "--replace", "f_{n}", "--apply")
    second = run("03", folder, "--find", "^.*$", "--replace", "g_{n}", "--apply")
    assert second.returncode == 0, second.stderr
    assert len(list(folder.glob("rename_undo_*.json"))) == 2


# --------------------------------------------------------------------------- 04 backup

def test_backup_rotation_only_touches_this_folders_archives(tmp_path, run):
    """Audit repro: --keep 1 for 'proj' deleted proj's new zip and counted proj_old_*.zip."""
    write(tmp_path / "proj" / "a.txt", "proj")
    write(tmp_path / "proj_old" / "b.txt", "old project")
    out = tmp_path / "out"
    assert run("04", tmp_path / "proj_old", out).returncode == 0
    for _ in range(3):
        result = run("04", tmp_path / "proj", out, "--keep", "1")
        assert result.returncode == 0, result.stderr
    proj = sorted(out.glob("proj_2*.zip"))
    other = sorted(out.glob("proj_old_*.zip"))
    assert len(proj) == 1 and len(other) == 1
    assert "1 backup(s) on disk for 'proj'" in result.stdout
    with zipfile.ZipFile(proj[0]) as zf:
        assert zf.read("proj/a.txt") == b"proj"


def test_backup_same_second_runs_never_overwrite_each_other(tmp_path):
    mod = load_script("04")
    src = tmp_path / "proj"
    write(src / "a.txt", "1")
    dest = tmp_path / "out"
    dest.mkdir()
    made = [mod.create_backup(src, dest, []) for _ in range(3)]
    assert len({p.name for p in made}) == 3
    assert all(p.exists() for p in made)
    assert mod.list_backups("proj", dest) == made, "list order is creation order"
    removed = mod.rotate("proj", dest, keep=1, protect=made[-1])
    assert removed == made[:2]
    assert mod.list_backups("proj", dest) == [made[-1]]


def test_backup_manifest_and_excludes(tmp_path, run):
    src = tmp_path / "proj"
    write(src / "src" / "main.py", "print(1)")
    write(src / "node_modules" / "lib.js", "junk")
    write(src / "debug.log", "junk")
    run("04", src, tmp_path / "out", "--exclude", "node_modules", "*.log")
    archive = next((tmp_path / "out").glob("proj_*.zip"))
    with zipfile.ZipFile(archive) as zf:
        names = set(zf.namelist())
        manifest = json.loads(zf.read("proj/_backup_manifest.json"))
    assert names == {"proj/src/main.py", "proj/_backup_manifest.json"}
    assert manifest["file_count"] == 1
    assert (manifest["files"][0]["path"], manifest["files"][0]["size"]) == ("src/main.py", 8)


def corrupt_member(archive: Path, member_suffix: str) -> None:
    with zipfile.ZipFile(archive) as zf:
        info = next(i for i in zf.infolist() if i.filename.endswith(member_suffix))
    data = bytearray(archive.read_bytes())
    name_len, extra_len = struct.unpack("<HH", data[info.header_offset + 26:info.header_offset + 30])
    data[info.header_offset + 30 + name_len + extra_len] ^= 0xFF
    archive.write_bytes(bytes(data))


def test_backup_verify_passes_then_catches_a_flipped_byte(tmp_path, run):
    write(tmp_path / "proj" / "notes.txt", "important " * 50)
    run("04", tmp_path / "proj", tmp_path / "out")
    archive = next((tmp_path / "out").glob("proj_*.zip"))

    ok = run("04", "--verify", archive)
    assert ok.returncode == 0, ok.stdout
    assert "OK" in ok.stdout

    corrupt_member(archive, "notes.txt")
    bad = run("04", "--verify", archive)
    assert bad.returncode == 1
    assert "corrupt member: proj/notes.txt" in bad.stdout


def test_backup_verify_detects_manifest_mismatch(tmp_path):
    mod = load_script("04")
    archive = tmp_path / "proj_20260101-000000.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("proj/a.txt", "abc")
        zf.writestr("proj/extra.txt", "surprise")
        zf.writestr("proj/_backup_manifest.json", json.dumps({
            "file_count": 2,
            "files": [{"path": "a.txt", "size": 99}, {"path": "gone.txt", "size": 1}]}))
    problems = mod.verify_zip(archive)
    assert "missing from archive: gone.txt" in problems
    assert "not in manifest: extra.txt" in problems
    assert any(p.startswith("size mismatch: a.txt") for p in problems)


def test_backup_verify_after_and_destination_guard(tmp_path, run):
    write(tmp_path / "proj" / "a.txt")
    good = run("04", tmp_path / "proj", tmp_path / "out", "--verify-after")
    assert good.returncode == 0 and "CRC and manifest match" in good.stdout
    inside = run("04", tmp_path / "proj", tmp_path / "proj" / "backups")
    assert inside.returncode == 1 and "outside the source" in inside.stderr


# --------------------------------------------------------------------------- 09 disk report

def test_disk_report_ranks_largest_first(tmp_path, run):
    write(tmp_path / "t" / "big" / "movie.bin", "x" * 50_000)
    write(tmp_path / "t" / "small" / "note.txt", "x" * 10)
    mod = load_script("09")
    dir_sizes, files, errors = mod.scan(tmp_path / "t")
    assert dir_sizes[str(tmp_path / "t")] == 50_010
    assert errors == 0 and len(files) == 2
    result = run("09", tmp_path / "t", "--top", "1")
    assert result.returncode == 0
    top_file = result.stdout.split("== Top 1 files ==")[1]
    assert "movie.bin" in top_file and "note.txt" not in top_file


# --------------------------------------------------------------------------- 10 downloads cleaner

@pytest.fixture
def downloads(tmp_path):
    folder = tmp_path / "Downloads"
    for name in ("report.pdf", "photo.jpg", "setup.exe", "desktop.ini", "Thumbs.db",
                 "big.iso.crdownload", "film.mkv.part", "draft.tmp"):
        write(folder / name, name, age_days=90)
    write(folder / "fresh.txt", "new", age_days=1)
    return folder


def test_cleaner_never_moves_metadata_or_partial_downloads(downloads, run):
    result = run("10", downloads, "--days", "30")
    assert result.returncode == 0
    listed = result.stdout
    for name in ("report.pdf", "photo.jpg", "setup.exe"):
        assert name in listed
    for name in ("desktop.ini", "Thumbs.db", "big.iso.crdownload", "film.mkv.part",
                 "draft.tmp", "fresh.txt"):
        assert name not in listed


def test_cleaner_apply_then_undo_restores_identical_tree(downloads, run):
    before = tree(downloads)
    applied = run("10", downloads, "--days", "30", "--apply")
    assert applied.returncode == 0, applied.stderr
    assert (downloads / "Archive").is_dir()
    assert not (downloads / "report.pdf").exists()
    log = next((downloads / "Archive").glob("cleaner_moves_*.json"))

    undone = run("10", "--undo", log)
    assert undone.returncode == 0, undone.stderr
    after = {k: v for k, v in tree(downloads).items() if not k.startswith("Archive/")}
    assert after == before
    assert dirs(downloads / "Archive") == set(), "month/category folders are pruned"


def test_cleaner_keeps_going_when_a_file_is_locked(downloads, monkeypatch, capsys):
    mod = load_script("10")
    real_rename = Path.rename

    def locked(self, target):
        if self.name == "photo.jpg":
            raise PermissionError(13, "being used by another process", str(self))
        return real_rename(self, target)

    monkeypatch.setattr(Path, "rename", locked)
    plan = mod.plan_moves(downloads, 30)
    log_path, failures = mod.apply_moves(plan, downloads / "Archive")
    assert failures == 1
    logged = {Path(e["from"]).name for e in json.loads(log_path.read_text())}
    assert logged == {"report.pdf", "setup.exe"}
    assert (downloads / "photo.jpg").exists()
