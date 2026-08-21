"""
Tests for the library configuration model — Stage F4.

THREE CLAIMS UNDER TEST, in the order they matter:

1. **With no config file, every value is the literal photo-flow used to hardcode.** The
   defaults are pinned here as strings rather than compared to `library_config`'s own
   constants, so a future edit to a default has to be made twice — once in the code and
   once here — instead of silently redefining "today's behaviour".
2. **A bad install file is refused, never partially applied.** A path in a config file is
   one typo away from a destructive operation running against the wrong tree, so every
   refusal below is a safety test, not an ergonomics test.
3. **A bad LIBRARY file degrades instead of raising.** Nothing in it can point an
   operation at a directory, and the same file is read by `photo_flow.collections`, which
   already degrades on a parse error and refuses the following write. One file may not
   have two contradictory failure modes.

Isolation: every test writes into `tmp_path` and reads it back through an explicit `path=`
argument or the `PHOTOFLOW_CONFIG` environment variable. Nothing here reads or writes
`~/.photoflow/config.toml`, `~/Pictures/photoflow.toml`, the real index or a photograph.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from photo_flow import config, library_config
from photo_flow.api.app import create_app
from photo_flow.library_config import LibraryConfigError

REPO_ROOT = Path(__file__).resolve().parents[1]


def write(path: Path, text: str) -> Path:
    """Write a config file and return its path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# 1. Defaults reproduce the hardcoded behaviour
# ---------------------------------------------------------------------------


class TestDefaults:
    """No file at all is the normal state, and it must change nothing."""

    def test_absent_file_yields_the_literals_config_py_used_to_hardcode(self, tmp_path):
        install = library_config.load_install(tmp_path / "nothing.toml")

        home = str(Path.home())
        assert install.present is False
        assert str(install.library_root) == f"{home}/Pictures"
        assert {key: str(value) for key, value in install.roots.items()} == {
            "camera": "/Volumes/Fuji X-T4/DCIM",
            "staging": f"{home}/Pictures/Staging",
            "final": f"{home}/Pictures/Final",
            "raws": "/Volumes/EXT/Bilder/RAWs",
            "videos": "/Volumes/EXT/Videos/Videos",
            "gallery": "/Users/johannes.krumm/SourceRoot/photo-flow/photo_gallery/src",
            "trash": f"{home}/Pictures/.photoflow-trash",
        }

    def test_the_default_camera_is_the_one_the_tool_was_written_against(self, tmp_path):
        install = library_config.load_install(tmp_path / "nothing.toml")

        camera = install.active_camera()
        assert camera.volume == "Fuji X-T4"
        assert str(camera.camera_path) == "/Volumes/Fuji X-T4/DCIM"
        assert set(camera.extensions) == {".JPG", ".RAF", ".MOV"}

    def test_absent_library_file_yields_the_default_axes(self, tmp_path):
        organisation = library_config.load_organisation(tmp_path / "photoflow.toml")

        assert organisation.present is False
        assert organisation.readable is True
        assert organisation.stage.mode == "folders"
        assert organisation.layout.mode == "flat"
        assert organisation.naming.template == "%Y-%m-%d_%H-%M-%S{n}_{base}"
        assert organisation.naming.apply == "import"
        assert organisation.unimplemented == ()

    def test_config_py_exposes_exactly_what_the_loader_resolved(self):
        """The module constants are the loader's answer, not a second copy of it."""
        assert config.CAMERA_PATH == config.INSTALL.roots["camera"]
        assert config.STAGING_PATH == config.INSTALL.roots["staging"]
        assert config.FINAL_PATH == config.INSTALL.roots["final"]
        assert config.RAWS_PATH == config.INSTALL.roots["raws"]
        assert config.SSD_PATH == config.INSTALL.roots["videos"]
        assert config.GALLERY_PATH == config.INSTALL.roots["gallery"]
        assert config.TRASH_PATH == config.INSTALL.roots["trash"]
        assert config.LIBRARY_ROOT == config.INSTALL.library_root
        assert config.LIBRARY_CONFIG_PATH == config.INSTALL.library_config_path
        assert config.CULL_ROOTS == {"final": config.FINAL_PATH, "staging": config.STAGING_PATH}
        assert config.EXTENSIONS == {".JPG", ".RAF", ".MOV"}


# ---------------------------------------------------------------------------
# 2. Reading a good file
# ---------------------------------------------------------------------------


class TestReadingInstall:
    """What a well-formed install file may say, and what it implies."""

    def test_library_root_moves_the_roots_that_hang_off_it(self, tmp_path):
        target = write(tmp_path / "config.toml", f'[library]\nroot = "{tmp_path}/Lib"\n')

        install = library_config.load_install(target)

        assert install.library_root == tmp_path / "Lib"
        assert install.roots["staging"] == tmp_path / "Lib" / "Staging"
        assert install.roots["final"] == tmp_path / "Lib" / "Final"
        assert install.roots["trash"] == tmp_path / "Lib" / ".photoflow-trash"
        assert install.library_config_path == tmp_path / "Lib" / "photoflow.toml"
        # The external volumes are NOT derived from the library root.
        assert str(install.roots["raws"]) == "/Volumes/EXT/Bilder/RAWs"

    def test_an_explicit_root_overrides_the_derived_one(self, tmp_path):
        target = write(
            tmp_path / "config.toml",
            f'[library]\nroot = "{tmp_path}/Lib"\n\n[roots]\nfinal = "{tmp_path}/Elsewhere"\n',
        )

        install = library_config.load_install(target)

        assert install.roots["final"] == tmp_path / "Elsewhere"
        assert install.roots["staging"] == tmp_path / "Lib" / "Staging"

    def test_a_tilde_is_expanded(self, tmp_path):
        target = write(tmp_path / "config.toml", '[library]\nroot = "~/PicturesTest"\n')

        install = library_config.load_install(target)

        assert install.library_root == Path.home() / "PicturesTest"

    def test_the_camera_supplies_the_camera_root(self, tmp_path):
        target = write(
            tmp_path / "config.toml",
            '[[cameras]]\nid = "sony"\nname = "A7"\nvolume = "SONY"\ndcim = "DCIM/100MSDCF"\n'
            'photos = [".jpg"]\nraws = [".arw"]\nvideos = [".mp4"]\n',
        )

        install = library_config.load_install(target)

        assert str(install.roots["camera"]) == "/Volumes/SONY/DCIM/100MSDCF"
        assert install.cameras[0].extensions == (".JPG", ".ARW", ".MP4")

    def test_an_explicit_camera_root_beats_the_profile(self, tmp_path):
        target = write(
            tmp_path / "config.toml",
            f'[roots]\ncamera = "{tmp_path}/CardReader"\n',
        )

        install = library_config.load_install(target)

        assert install.roots["camera"] == tmp_path / "CardReader"

    def test_active_camera_is_the_mounted_one(self, tmp_path, monkeypatch):
        target = write(
            tmp_path / "config.toml",
            '[[cameras]]\nid = "absent"\nvolume = "Nothing Here"\n\n'
            '[[cameras]]\nid = "present"\nvolume = "Also Nothing"\n',
        )
        install = library_config.load_install(target)
        monkeypatch.setattr(
            library_config.CameraProfile,
            "is_connected",
            lambda self: self.id == "present",
        )

        assert install.active_camera().id == "present"

    def test_active_camera_falls_back_to_the_first_when_nothing_is_mounted(self, tmp_path):
        target = write(
            tmp_path / "config.toml",
            '[[cameras]]\nid = "one"\nvolume = "Absent One"\n\n'
            '[[cameras]]\nid = "two"\nvolume = "Absent Two"\n',
        )

        assert library_config.load_install(target).active_camera().id == "one"


class TestReadingOrganisation:
    """The three axes of decision 0003, read from the library file."""

    def test_all_three_axes_round_trip(self, tmp_path):
        target = write(
            tmp_path / "photoflow.toml",
            '[stage]\nmode = "in-place"\ntag = "sf:stage"\n\n'
            '[layout]\nmode = "year-month"\n\n'
            '[naming]\ntemplate = "%Y%m%d_{base}"\napply = "never"\n',
        )

        organisation = library_config.load_organisation(target)

        assert organisation.present is True
        assert organisation.readable is True
        assert organisation.stage.mode == "in-place"
        assert organisation.stage.tag == "sf:stage"
        assert organisation.layout.mode == "year-month"
        assert organisation.naming.template == "%Y%m%d_{base}"
        assert organisation.naming.apply == "never"

    def test_as_is_is_a_layout(self, tmp_path):
        """0003 leaves 'no opinion, leave the files alone' open; it is modelled as `as-is`."""
        target = write(tmp_path / "photoflow.toml", '[layout]\nmode = "as-is"\n')

        assert library_config.load_organisation(target).layout.mode == "as-is"

    def test_a_non_default_axis_is_reported_as_unimplemented(self, tmp_path):
        target = write(
            tmp_path / "photoflow.toml",
            '[stage]\nmode = "in-place"\n\n[layout]\nmode = "album"\n',
        )

        pending = library_config.load_organisation(target).unimplemented

        assert pending == ("stage.mode = 'in-place'", "layout.mode = 'album'")

    def test_the_collections_table_is_ignored_not_rejected(self, tmp_path):
        """The library file is SHARED with `photo_flow.collections` and with hand notes."""
        target = write(
            tmp_path / "photoflow.toml",
            "# my notes\n[[collections]]\nid = 'keepers'\nname = 'Keepers'\n\n"
            "[collections.query]\nrating_min = 4\n\n[layout]\nmode = 'album'\n\n"
            "[something.invented]\nkey = 1\n",
        )

        organisation = library_config.load_organisation(target)

        assert organisation.readable is True
        assert organisation.layout.mode == "album"
        assert organisation.errors == ()


# ---------------------------------------------------------------------------
# 3. Refusals — every one of these is a safety test
# ---------------------------------------------------------------------------


class TestInstallRefusals:
    """A bad install file must fail loudly, and must not be partially applied."""

    @pytest.mark.parametrize(
        "body, fragment",
        [
            ('[library]\nroot = "Pictures"\n', "absolute path"),
            ('[library]\nroot = "/"\n', "filesystem root"),
            ('[library]\nroot = "~"\n', "home directory"),
            ('[library]\nroot = 7\n', "non-empty string"),
            ('[library]\nroot = ""\n', "non-empty string"),
        ],
    )
    def test_a_bad_library_root_is_refused(self, tmp_path, body, fragment):
        target = write(tmp_path / "config.toml", body)

        with pytest.raises(LibraryConfigError) as excinfo:
            library_config.load_install(target)

        assert fragment in str(excinfo.value)
        assert str(target) in str(excinfo.value)

    def test_a_relative_root_is_refused(self, tmp_path):
        target = write(tmp_path / "config.toml", '[roots]\nfinal = "Pictures/Final"\n')

        with pytest.raises(LibraryConfigError, match="roots.final must be an absolute path"):
            library_config.load_install(target)

    def test_a_non_string_root_is_refused(self, tmp_path):
        target = write(tmp_path / "config.toml", "[roots]\nfinal = 4\n")

        with pytest.raises(LibraryConfigError, match="roots.final must be a string path"):
            library_config.load_install(target)

    def test_two_roots_naming_the_same_directory_are_refused(self, tmp_path):
        """`final == staging` turns finalize into a copy onto itself followed by a delete."""
        target = write(
            tmp_path / "config.toml",
            f'[roots]\nfinal = "{tmp_path}/Same"\nstaging = "{tmp_path}/Same"\n',
        )

        with pytest.raises(LibraryConfigError, match="the same directory"):
            library_config.load_install(target)

    def test_a_culling_root_inside_the_other_is_refused(self, tmp_path):
        """Every scan in the tool is recursive, so nesting makes finalize rediscover itself."""
        target = write(
            tmp_path / "config.toml",
            f'[roots]\nfinal = "{tmp_path}/Lib"\nstaging = "{tmp_path}/Lib/Staging"\n',
        )

        with pytest.raises(LibraryConfigError, match="must not contain each other"):
            library_config.load_install(target)

    def test_the_trash_inside_a_culling_root_is_refused(self, tmp_path):
        """A trashed photo under Final would be re-indexed as a live one."""
        target = write(
            tmp_path / "config.toml",
            f'[roots]\nfinal = "{tmp_path}/Final"\ntrash = "{tmp_path}/Final/.trash"\n',
        )

        with pytest.raises(LibraryConfigError, match="roots.trash must not be inside roots.final"):
            library_config.load_install(target)

    def test_a_culling_root_inside_the_trash_is_refused(self, tmp_path):
        """Purging the trash would then reach live files."""
        target = write(
            tmp_path / "config.toml",
            f'[roots]\ntrash = "{tmp_path}/T"\nstaging = "{tmp_path}/T/Staging"\n',
        )

        with pytest.raises(LibraryConfigError, match="roots.staging must not be inside roots.trash"):
            library_config.load_install(target)

    @pytest.mark.parametrize(
        "inner, outer",
        [
            # `import` MOVES every JPG, RAF and MOV off the camera root and deletes the
            # originals — so a camera root under Final empties Final. This one loaded
            # clean until the general no-nesting rule replaced the three named pairs.
            ("camera", "final"),
            ("camera", "staging"),
            # `sync_gallery` deletes gallery images that no longer rate 4+; with Final
            # inside the gallery those deletions land on the masters.
            ("final", "gallery"),
            # `cleanup` deletes every RAW under the RAW root that no JPG claims.
            ("raws", "final"),
            ("videos", "raws"),
            ("trash", "gallery"),
        ],
    )
    def test_no_root_may_sit_inside_another(self, tmp_path, inner, outer):
        """Each root is a distinct pipeline stage, and every operation on one recurses."""
        target = write(
            tmp_path / "config.toml",
            f'[roots]\n{outer} = "{tmp_path}/Outer"\n{inner} = "{tmp_path}/Outer/Inner"\n',
        )

        with pytest.raises(LibraryConfigError) as excinfo:
            library_config.load_install(target)

        message = str(excinfo.value)
        assert f"roots.{inner} must not be inside roots.{outer}" in message
        assert str(tmp_path / "Outer" / "Inner") in message   # which directory
        assert str(target) in message                          # which file to open

    def test_a_root_that_is_a_file_is_refused_by_the_loader(self, tmp_path):
        existing = tmp_path / "Final.jpg"
        existing.write_text("x", encoding="utf-8")
        target = write(tmp_path / "config.toml", f'[roots]\nfinal = "{existing}"\n')

        with pytest.raises(LibraryConfigError) as excinfo:
            library_config.load_install(target)

        assert "roots.final" in str(excinfo.value)
        assert "not a directory" in str(excinfo.value)

    @pytest.mark.parametrize(
        "body, fragment",
        [
            ('[rootz]\nfinal = "/tmp/x"\n', "unknown key 'rootz'"),
            ('[roots]\nfinl = "/tmp/x"\n', "unknown key 'finl'"),
            ('[library]\nrooot = "/tmp/x"\n', "unknown key 'rooot'"),
            ('[[cameras]]\nvolume = "X"\nlens = "50mm"\n', "unknown key 'lens'"),
        ],
    )
    def test_a_misspelled_key_is_refused_and_the_message_names_the_alternatives(
        self, tmp_path, body, fragment
    ):
        """A misspelled setting reads as 'I configured that' and behaves as 'I did not'."""
        target = write(tmp_path / "config.toml", body)

        with pytest.raises(LibraryConfigError) as excinfo:
            library_config.load_install(target)

        assert fragment in str(excinfo.value)
        assert "allowed:" in str(excinfo.value)

    @pytest.mark.parametrize(
        "body, fragment",
        [
            ('[[cameras]]\nname = "No volume"\n', "needs a volume"),
            ('[[cameras]]\nvolume = "/Volumes/X"\n', "volume NAME, not a path"),
            ('[[cameras]]\nvolume = "X"\ndcim = "/DCIM"\n', "relative to the volume"),
            ('[[cameras]]\nvolume = "X"\nphotos = "JPG"\n', "list of strings"),
            ('[[cameras]]\nvolume = "X"\nphotos = ["JPG"]\n', "not a file extension"),
            ('[[cameras]]\nid = "a"\nvolume = "X"\n\n[[cameras]]\nid = "a"\nvolume = "Y"\n',
             "duplicate camera id"),
            ('cameras = 3\n', "array of tables"),
        ],
    )
    def test_a_bad_camera_profile_is_refused(self, tmp_path, body, fragment):
        target = write(tmp_path / "config.toml", body)

        with pytest.raises(LibraryConfigError) as excinfo:
            library_config.load_install(target)

        assert fragment in str(excinfo.value)

    def test_malformed_toml_is_refused(self, tmp_path):
        target = write(tmp_path / "config.toml", "[roots\nfinal = ")

        with pytest.raises(LibraryConfigError, match="is not valid TOML"):
            library_config.load_install(target)

    def test_nothing_is_applied_when_anything_is_wrong(self, tmp_path):
        """
        All-or-nothing. A half-read roots table is the one outcome that could point an
        operation at a directory nobody named.
        """
        target = write(
            tmp_path / "config.toml",
            f'[roots]\nfinal = "{tmp_path}/Good"\nstaging = "relative/bad"\n',
        )

        with pytest.raises(LibraryConfigError):
            library_config.load_install(target)

        # The good half is not visible anywhere: the loader returns a value or it raises.
        fallback = library_config.load_install(tmp_path / "absent.toml")
        assert fallback.roots["final"] != tmp_path / "Good"


# ---------------------------------------------------------------------------
# 3b. The root guard
#
# Its own section because it is the one refusal whose failure mode is not a broken
# workflow but a disclosure: `sync_gallery` copies every rating>=4 JPG under `roots.final`
# to a PUBLIC host and `backup` rsyncs the same tree off the machine, so a config file —
# a plain text file, hand-edited by design — that repoints `final` at `/Users` turns a
# routine publish into an exfiltration. `import` MOVES files off `roots.camera` and
# deletes the originals, so a camera root of `/Volumes/EXT` empties an external disk.
#
# Both halves are tested, and the acceptance half matters as much as the refusal half:
# a guard tightened until it refuses the real library is a guard that gets deleted.
# ---------------------------------------------------------------------------


HOME = str(Path.home())


class TestRootGuardAccepts:
    """The daily driver, and every shape of legitimate root, must load."""

    def test_every_root_of_the_running_install_is_accepted(self):
        """
        The real, resolved configuration of this machine passes the guard.

        This is the anti-tightening test: it reads whatever `photo_flow.config` actually
        resolved — the live `~/.photoflow/config.toml` if there is one, the built-in
        defaults if not — so no future refusal can be added without noticing that it
        stops the tool it protects.
        """
        for key, root in config.INSTALL.roots.items():
            assert library_config.root_refusal(root) is None, f"roots.{key} = {root}"
        assert library_config.library_root_refusal(config.INSTALL.library_root) is None

    @pytest.mark.parametrize(
        "candidate",
        [
            f"{HOME}/Pictures",
            f"{HOME}/Pictures/Final",
            f"{HOME}/Pictures/.photoflow-trash",
            f"{HOME}/SourceRoot/photo-flow/photo_gallery/src",
            "/Volumes/EXT/Bilder/RAWs",              # RAW drive: /Volumes is unreachable
            "/Volumes/EXT/Videos/Videos",            # here, which the guard must not need
            "/Volumes/Fuji X-T4/DCIM",               # camera card, mounted or not
            "/Volumes/Photos/Fuji",                  # a library on a dedicated disk
            f"{HOME}/Dropbox/Photography/Keepers",
        ],
    )
    def test_a_legitimate_root_is_accepted(self, candidate):
        assert library_config.root_refusal(Path(candidate)) is None

    def test_a_root_that_does_not_exist_yet_is_accepted(self, tmp_path):
        """Import creates its destinations, so a root is routinely named before it exists."""
        assert library_config.root_refusal(tmp_path / "not" / "yet" / "here") is None

    def test_the_live_seven_roots_survive_the_no_nesting_rule(self, tmp_path):
        """
        The anti-tightening test for the containment rule, run through the public loader:
        the seven roots this machine actually uses are written into a config file and read
        back. If "no root inside another" ever collides with a real layout, this is where
        it shows up — before the guard is deleted for being in the way.
        """
        body = "[roots]\n" + "".join(
            f'{key} = "{config.INSTALL.roots[key]}"\n' for key in library_config.ROOT_KEYS
        )
        target = write(tmp_path / "config.toml", body)

        install = library_config.load_install(target)

        assert install.roots == config.INSTALL.roots

    def test_the_scratch_exemption_still_covers_the_real_scratch_directory(self, tmp_path):
        """
        macOS puts the scratch directory inside `/private`, a protected tree — so without
        the exemption every fixture in this file, and every throwaway library, is refused.
        """
        assert str(tmp_path.resolve()).startswith(str(Path(tempfile.gettempdir()).resolve()))
        assert library_config.root_refusal(tmp_path / "Library" / "Final") is None

    def test_a_whole_volume_is_a_legitimate_LIBRARY_root(self, tmp_path):
        """
        `library.root` is derived from, never scanned — so a library at the top of a
        dedicated external disk stays expressible even though the same path is refused
        as a *root*, where it would mean "publish this whole disk".
        """
        assert library_config.library_root_refusal(Path("/Volumes/Photos")) is None
        assert library_config.root_refusal(Path("/Volumes/Photos")) is not None

        target = write(tmp_path / "config.toml", '[library]\nroot = "/Volumes/Photos"\n')
        install = library_config.load_install(target)

        assert install.roots["final"] == Path("/Volumes/Photos/Final")

    def test_the_whole_real_default_configuration_still_loads(self, tmp_path, monkeypatch):
        """The file `photoflow config init` writes must survive the guard verbatim."""
        written = library_config.write_install_template(tmp_path / "config.toml")

        install = library_config.load_install(written)

        assert install.roots == library_config.load_install(tmp_path / "absent.toml").roots


class TestRootGuardRefuses:
    """Each of these is a directory that must never be published, backed up or emptied."""

    @pytest.mark.parametrize(
        "candidate, fragment",
        [
            ("/", "filesystem root"),
            ("/Users", "where all user accounts live"),
            ("/Volumes", "where all mounted volumes live"),
            ("/net", "where all mounted network shares live"),
            ("/Users/someone-else", "entire user account"),
            ("/Volumes/EXT", "entire mounted volume"),
            ("/Volumes/Time Machine", "entire mounted volume"),
            ("/Applications", "top-level system directory"),
            ("/opt", "top-level system directory"),
            ("/System/Library/Fonts", "holds the system"),
            ("/usr/local/share/photos", "holds the system"),
            ("/private/etc", "holds the system"),
            (f"{HOME}/Library/Mail", "holds the system"),
            (f"{HOME}/.ssh", "holds the system"),
            (f"{HOME}/.config/photos", "holds the system"),
            (HOME, "home directory itself"),
            ("Pictures/Final", "must be an absolute path"),
        ],
    )
    def test_a_dangerous_root_is_refused(self, candidate, fragment):
        reason = library_config.root_refusal(Path(candidate))

        assert reason is not None, f"{candidate} was accepted"
        assert fragment in reason

    def test_the_home_directory_is_refused_as_a_library_root_too(self):
        """The weaker library-root rule still keeps the whole account out."""
        assert library_config.library_root_refusal(Path(HOME)) is not None
        assert library_config.library_root_refusal(Path(f"{HOME}/Library")) is not None

    @pytest.mark.parametrize(
        "candidate",
        [
            "/Users/",                    # trailing slash
            "/Volumes/EXT/",
            f"{HOME}/Pictures/../..",     # .. climbing out
            f"{HOME}/Pictures/Final/../../../..",
            "/Users/./",
        ],
    )
    def test_normalisation_cannot_be_used_to_walk_past_the_guard(self, candidate):
        assert library_config.root_refusal(Path(candidate)) is not None

    @pytest.mark.parametrize("candidate", ["/users", "/USERS/nobody", "/VOLUMES/EXT", "/uSr/x"])
    def test_a_case_difference_cannot_be_used_to_walk_past_the_guard(self, candidate):
        """
        macOS's default filesystem is case-insensitive and `resolve()` does not normalise
        case, so every comparison is casefolded. On a case-sensitive filesystem this can
        only refuse a path differing from a system directory by case alone — the safe
        direction to be wrong in.
        """
        assert library_config.root_refusal(Path(candidate)) is not None

    def test_a_root_that_is_a_file_is_refused(self, tmp_path):
        """
        A root that does not exist yet is legitimate — import creates its destinations —
        but a root that exists and is not a directory is a typo that happened to land on
        something, and every operation over it would scan nothing or write inside a file.
        """
        target = tmp_path / "Final"
        target.write_text("not a library", encoding="utf-8")

        reason = library_config.root_refusal(target)

        assert reason is not None and "not a directory" in reason

    def test_a_symlink_to_a_file_is_refused_too(self, tmp_path):
        """Resolution happens first, so the guard judges what the link points at."""
        real = tmp_path / "notes.txt"
        real.write_text("x", encoding="utf-8")
        link = tmp_path / "Final"
        link.symlink_to(real)

        assert library_config.root_refusal(link) is not None

    @pytest.mark.parametrize("scratch", [lambda: str(Path.home()), lambda: "/", lambda: "/Volumes"])
    def test_TMPDIR_cannot_switch_the_protected_trees_off(self, monkeypatch, scratch):
        """
        The scratch exemption is the guard's one concession, and `tempfile.gettempdir()`
        honours `TMPDIR` — so an environment variable used to be able to disable the
        protected-tree clause wholesale. Measured before the qualification existed: with
        `TMPDIR=$HOME`, `~/Library/Mail` and `~/.ssh` were both accepted as photo roots.
        """
        monkeypatch.setattr(library_config.tempfile, "gettempdir", scratch)

        assert library_config._scratch_root() is None
        for candidate in (f"{HOME}/Library/Mail", f"{HOME}/.ssh", "/System/Library/Fonts"):
            assert library_config.root_refusal(Path(candidate)) is not None, candidate

    def test_a_symlink_is_followed_before_the_check(self, tmp_path):
        """Otherwise a directory named `Final` in the config is whatever it points at."""
        link = tmp_path / "Final"
        link.symlink_to(Path("/Users"))

        assert library_config.root_refusal(link) is not None

    def test_a_symlinked_root_is_stored_as_what_it_resolves_to(self, tmp_path):
        """
        The path that was checked is the path that gets used. Storing the user's own words
        and checking the resolved ones would leave the guard describing a different
        directory from the one every operation walks.
        """
        real = tmp_path / "RealLibrary" / "Final"
        real.mkdir(parents=True)
        link = tmp_path / "shortcut"
        link.symlink_to(real)
        target = write(tmp_path / "config.toml", f'[roots]\nfinal = "{link}"\n')

        install = library_config.load_install(target)

        assert install.roots["final"] == real


class TestRootGuardInTheLoader:
    """The guard must be reached by every path a root can arrive on, and must be fatal."""

    def test_a_configured_root_is_refused_and_the_message_names_key_and_value(self, tmp_path):
        target = write(tmp_path / "config.toml", '[roots]\nfinal = "/Users"\n')

        with pytest.raises(LibraryConfigError) as excinfo:
            library_config.load_install(target)

        message = str(excinfo.value)
        assert str(target) in message          # which file to open
        assert "roots.final" in message        # which line
        assert "'/Users'" in message           # which value

    def test_a_root_DERIVED_from_the_library_root_is_refused(self, tmp_path):
        """`library.root = "/Users"` names no dangerous root; it derives three."""
        target = write(tmp_path / "config.toml", '[library]\nroot = "/Users"\n')

        with pytest.raises(LibraryConfigError) as excinfo:
            library_config.load_install(target)

        assert "roots.staging = '/Users/Staging'" in str(excinfo.value)
        assert "derived from library.root" in str(excinfo.value)

    def test_a_camera_profile_cannot_smuggle_in_a_whole_volume(self, tmp_path):
        """
        `import` moves every JPG, RAF and MOV off the camera root and DELETES the
        originals, so a camera root of `/Volumes/EXT` empties an external disk. That path
        never passes through the per-value check — it is assembled from the profile.
        """
        target = write(tmp_path / "config.toml", '[[cameras]]\nvolume = "EXT"\ndcim = "."\n')

        with pytest.raises(LibraryConfigError) as excinfo:
            library_config.load_install(target)

        assert "roots.camera = '/Volumes/EXT'" in str(excinfo.value)
        assert "derived from [[cameras]]" in str(excinfo.value)

    def test_a_camera_dcim_may_not_climb_out_of_its_volume(self, tmp_path):
        target = write(tmp_path / "config.toml", '[[cameras]]\nvolume = "EXT"\ndcim = "../.."\n')

        with pytest.raises(LibraryConfigError, match="must stay inside the volume"):
            library_config.load_install(target)

    def test_the_overlap_check_is_case_insensitive_too(self, tmp_path):
        """On a case-insensitive filesystem these two spellings are one directory."""
        target = write(
            tmp_path / "config.toml",
            f'[roots]\nfinal = "{tmp_path}/Lib/Final"\nstaging = "{tmp_path}/Lib/FINAL"\n',
        )

        with pytest.raises(LibraryConfigError, match="the same directory"):
            library_config.load_install(target)

    def test_a_refused_root_is_fatal_and_never_falls_back(self, tmp_path):
        """
        A silent fallback is its own hazard: the user believes the op ran against the
        configured tree, and it ran against another one. The import must die.
        """
        broken = write(tmp_path / "config.toml", '[roots]\nfinal = "/Users"\n')

        result = _run_python("import photo_flow.config as c; print(c.FINAL_PATH)", broken)

        assert result.returncode != 0
        assert "roots.final" in result.stderr
        assert str(Path.home() / "Pictures" / "Final") not in result.stdout

    def test_the_cli_reports_a_refused_root_as_one_actionable_line(self, tmp_path):
        broken = write(tmp_path / "config.toml", '[roots]\ngallery = "/Users/someone-else"\n')

        result = _run_python("import photo_flow.cli", broken)

        assert result.returncode == 2
        assert "will not start with this configuration" in result.stdout + result.stderr
        assert "roots.gallery" in result.stdout + result.stderr
        assert "Traceback" not in result.stderr


class TestOrganisationRefusals:
    """The library file reports rather than raises — but it still reports."""

    def test_malformed_toml_degrades_to_defaults_and_says_so(self, tmp_path):
        target = write(tmp_path / "photoflow.toml", "[layout\nmode = ")

        organisation = library_config.load_organisation(target)

        assert organisation.readable is False
        assert organisation.layout.mode == "flat"
        assert len(organisation.errors) == 1
        assert "is not valid TOML" in organisation.errors[0]

    @pytest.mark.parametrize(
        "body, fragment",
        [
            ('[stage]\nmode = "tags"\n', "stage.mode must be one of"),
            ('[stage]\nmoed = "folders"\n', "unknown key 'moed'"),
            ('[layout]\nmode = "yyyy-mm"\n', "layout.mode must be one of"),
            ('[naming]\napply = "always"\n', "naming.apply must be one of"),
            ('[naming]\ntemplate = "%Y-%m-%d"\n', "must contain {base}"),
            ('[naming]\ntemplate = "%Y/{base}"\n', "must not contain a separator"),
            ('[naming]\ntemplate = "{camera}_{base}"\n', "unknown placeholder"),
        ],
    )
    def test_a_bad_axis_is_reported_and_the_defaults_stand(self, tmp_path, body, fragment):
        target = write(tmp_path / "photoflow.toml", body)

        organisation = library_config.load_organisation(target)

        assert organisation.readable is False
        assert fragment in organisation.errors[0]
        assert organisation.stage.mode == "folders"
        assert organisation.layout.mode == "flat"
        assert organisation.naming.template == "%Y-%m-%d_%H-%M-%S{n}_{base}"

    def test_the_default_template_reproduces_todays_filenames(self):
        """
        The template is the serialisation of `timestamp_renamer`, not a new idea.

        `YYYY-MM-DD_HH-MM-SS_<base>`, with the collision counter between the seconds and
        the base — which is where `generate_timestamped_filename` puts it.
        """
        template = library_config.DEFAULT_NAMING_TEMPLATE
        from datetime import datetime

        stamp = datetime(2026, 1, 28, 10, 29, 15)
        rendered = stamp.strftime(template).replace("{n}", "").replace("{base}", "DSCF1234")
        assert rendered == "2026-01-28_10-29-15_DSCF1234"

        collided = stamp.strftime(template).replace("{n}", "-2").replace("{base}", "DSCF1234")
        assert collided == "2026-01-28_10-29-15-2_DSCF1234"


# ---------------------------------------------------------------------------
# 4. Fatality — a bad install file must stop the process, not be worked around
# ---------------------------------------------------------------------------


def _run_python(code: str, config_path: Path) -> subprocess.CompletedProcess:
    """Run a snippet in a fresh interpreter with PHOTOFLOW_CONFIG pointed at a file."""
    environment = dict(os.environ, PHOTOFLOW_CONFIG=str(config_path))
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(REPO_ROOT),
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
    )


class TestFatality:
    """
    Importing `photo_flow.config` with a broken install file must fail, not fall back.

    Run in a subprocess because that is the only honest way to test import-time behaviour:
    the module is already imported in this interpreter, and reloading it would leave every
    `from photo_flow.config import X` in the process bound to the old values.
    """

    def test_a_broken_install_file_stops_the_import(self, tmp_path):
        broken = write(tmp_path / "config.toml", '[roots]\nfinal = "not/absolute"\n')

        result = _run_python("import photo_flow.config", broken)

        assert result.returncode != 0
        assert "roots.final must be an absolute path" in result.stderr

    def test_the_cli_turns_that_into_one_actionable_line(self, tmp_path):
        broken = write(tmp_path / "config.toml", '[roots]\nfinal = "not/absolute"\n')

        result = _run_python("import photo_flow.cli", broken)

        assert result.returncode == 2
        assert "will not start with this configuration" in result.stdout + result.stderr
        assert "Traceback" not in result.stderr

    def test_a_good_file_imports_cleanly(self, tmp_path):
        good = write(tmp_path / "config.toml", f'[library]\nroot = "{tmp_path}/Lib"\n')

        result = _run_python(
            "import photo_flow.config as c; print(c.FINAL_PATH)", good
        )

        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == f"{tmp_path}/Lib/Final"

    def test_the_env_override_is_read_at_call_time(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PHOTOFLOW_CONFIG", str(tmp_path / "elsewhere.toml"))

        assert library_config.install_path() == tmp_path / "elsewhere.toml"


# ---------------------------------------------------------------------------
# 5. Writing the template
# ---------------------------------------------------------------------------


class TestTemplate:
    """`config init` writes the defaults, and never overwrites a hand-edited file."""

    def test_the_written_template_round_trips_to_the_same_values(self, tmp_path):
        target = tmp_path / "config.toml"

        library_config.write_install_template(target)
        loaded = library_config.load_install(target)
        defaults = library_config.load_install(tmp_path / "absent.toml")

        assert loaded.present is True
        assert loaded.library_root == defaults.library_root
        assert loaded.roots == defaults.roots
        assert loaded.cameras == defaults.cameras

    def test_it_refuses_to_overwrite(self, tmp_path):
        target = write(tmp_path / "config.toml", "# mine\n")

        with pytest.raises(LibraryConfigError, match="already exists"):
            library_config.write_install_template(target)

        assert target.read_text() == "# mine\n"

    def test_a_path_with_a_quote_survives_the_round_trip(self, tmp_path):
        odd = tmp_path / 'we"ird'
        install = library_config.load_install(tmp_path / "absent.toml")
        rendered = library_config.render_install(
            library_config.InstallConfig(
                path=tmp_path / "config.toml",
                present=True,
                library_root=odd,
                roots={**install.roots, "staging": odd / "Staging"},
                cameras=install.cameras,
            )
        )
        target = write(tmp_path / "config.toml", rendered)

        assert library_config.load_install(target).roots["staging"] == odd / "Staging"


# ---------------------------------------------------------------------------
# 6. The API surface
# ---------------------------------------------------------------------------


class TestConfigEndpoint:
    """`GET /api/config` reports what the running process resolved."""

    @pytest.fixture()
    def client(self):
        with TestClient(create_app()) as test_client:
            yield test_client

    def test_it_answers_json_not_the_spa(self, client):
        response = client.get("/api/config")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/json")

    def test_it_reports_both_halves_and_their_health(self, client):
        body = client.get("/api/config").json()

        assert body["install"]["roots"]["final"] == str(config.FINAL_PATH)
        assert body["install"]["library_root"] == str(config.LIBRARY_ROOT)
        assert body["library"]["path"] == str(config.LIBRARY_CONFIG_PATH)
        assert body["library"]["stage"]["mode"] in library_config.STAGE_MODES
        assert body["valid"] is config.ORGANISATION.readable
        assert body["unimplemented"] == list(config.ORGANISATION.unimplemented)

    def test_it_is_read_only(self, client):
        """No write surface: a mis-clicked roots table is a restore, not an undo."""
        assert client.post("/api/config", json={}).status_code == 405
        assert client.put("/api/config", json={}).status_code == 405
        assert client.delete("/api/config").status_code == 405


# ---------------------------------------------------------------------------
# 7. The cardinal rule of this stage
# ---------------------------------------------------------------------------


def test_loading_a_configuration_touches_no_file(tmp_path):
    """
    Reading configuration must not create, move or modify anything on disk.

    Stage F is a prototype stage over an irreplaceable library; the one thing no link in
    it may do is write to the library. The loader is a reader.
    """
    library = tmp_path / "Lib"
    (library / "Final").mkdir(parents=True)
    photo = library / "Final" / "keeper.JPG"
    photo.write_bytes(b"pixels")
    before = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*"))
    stat_before = photo.stat()

    target = write(tmp_path / "config.toml", f'[library]\nroot = "{library}"\n')
    install = library_config.load_install(target)
    library_config.load_organisation(install.library_config_path)

    after = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*"))
    assert after == sorted(before + [Path("config.toml")])
    assert photo.read_bytes() == b"pixels"
    assert photo.stat().st_mtime_ns == stat_before.st_mtime_ns
