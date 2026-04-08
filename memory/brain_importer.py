"""BrainImporter — import a folder of .md files into the vault as brain notes.

Usage:
    importer = BrainImporter(vault)
    result   = importer.import_folder("/path/to/brain")
    # result.imported: list of vault-relative paths written
    # result.skipped:  list of files that failed
    # result.corpus:   flat text corpus for profiling
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from memory.vault import MemoryVault

# Frontmatter pattern (standard YAML fences)
_FM_RE = re.compile(r"^---\s*\n.*?\n---\s*\n", re.DOTALL)


def _strip_frontmatter(text: str) -> str:
    m = _FM_RE.match(text)
    return text[m.end():].strip() if m else text.strip()


def _title_from_path(path: str) -> str:
    """Derive a human-readable title from a file path."""
    name = os.path.splitext(os.path.basename(path))[0]
    return name.replace("-", " ").replace("_", " ").title()


def _collect_md_files(root: str) -> list[str]:
    """Recursively collect all .md files under root, sorted."""
    paths = []
    for dirpath, _dirs, files in os.walk(root):
        for fname in files:
            if fname.lower().endswith(".md"):
                paths.append(os.path.join(dirpath, fname))
    return sorted(paths)


@dataclass
class ImportResult:
    imported:   list[str] = field(default_factory=list)   # vault-relative paths
    skipped:    list[str] = field(default_factory=list)    # source paths that failed
    corpus:     list[str] = field(default_factory=list)    # text chunks for profiling

    @property
    def total(self) -> int:
        return len(self.imported) + len(self.skipped)


class BrainImporter:
    """Imports external .md notes into the vault under brain/<subfolder>."""

    BRAIN_PREFIX = "brain"

    def __init__(self, vault: MemoryVault) -> None:
        self._vault = vault

    def import_folder(self, folder_path: str) -> ImportResult:
        """
        Walk folder_path, copy every .md file into vault/brain/...
        preserving the relative directory structure.

        Returns an ImportResult with imported paths and a text corpus
        suitable for passing to ProfileAnalyzer.
        """
        folder_path = os.path.realpath(folder_path)
        result      = ImportResult()

        if not os.path.isdir(folder_path):
            result.skipped.append(folder_path)
            return result

        md_files = _collect_md_files(folder_path)

        for src_path in md_files:
            try:
                # Build a vault-relative path: brain/<rel_to_folder>
                rel = os.path.relpath(src_path, folder_path)
                # Strip .md extension for vault key
                rel_no_ext = os.path.splitext(rel)[0]
                vault_rel  = os.path.join(self.BRAIN_PREFIX, rel_no_ext)

                with open(src_path, encoding="utf-8", errors="replace") as f:
                    raw = f.read()

                body  = _strip_frontmatter(raw)
                title = _title_from_path(src_path)

                # Upsert: overwrite if already exists
                existing = self._vault.get_note(vault_rel)
                if existing is not None:
                    existing.content = existing.content.split("\n\n", 1)[0] + "\n\n" + body
                    self._vault.save_note(existing)
                else:
                    self._vault.create_note(
                        vault_rel,
                        title=title,
                        body=body,
                        tags=["brain", "imported"],
                        note_type="brain",
                    )

                result.imported.append(vault_rel)

                # Add to corpus — split into non-empty paragraphs
                for chunk in re.split(r"\n{2,}", body):
                    chunk = chunk.strip()
                    if len(chunk) > 20:
                        result.corpus.append(chunk[:500])

            except Exception:
                result.skipped.append(src_path)

        return result

    def import_file(self, file_path: str) -> ImportResult:
        """Import a single .md file."""
        result = ImportResult()
        if not os.path.isfile(file_path):
            result.skipped.append(file_path)
            return result
        # Treat the parent folder as a single-file import
        folder   = os.path.dirname(file_path)
        fname    = os.path.basename(file_path)
        all_res  = self.import_folder(folder)
        # Filter to only the requested file
        vault_rel = os.path.join(
            self.BRAIN_PREFIX,
            os.path.splitext(fname)[0],
        )
        if vault_rel in all_res.imported:
            result.imported = [vault_rel]
            result.corpus   = all_res.corpus
        else:
            result.skipped = [file_path]
        return result
