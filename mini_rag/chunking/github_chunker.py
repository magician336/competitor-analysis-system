"""Release-, issue- and comment-aware GitHub chunking."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from dateutil import parser as date_parser

from schemas.document import SourceType, StructuredDocument

from .base_chunker import BaseChunker, TextSection


_COMMENT_RE = re.compile(
    r"(?m)^Comment by (?P<author>[^\r\n]+?)"
    r"(?: at (?P<time>\d{4}-\d{2}-\d{2}T[^\r\n ]+))?[ \t]*(?:\r?\n|$)"
)


class GitHubChunker(BaseChunker):
    """Keep issue bodies, individual comments and releases independently citable."""

    def _sections(self, document: StructuredDocument) -> list[TextSection]:
        if document.source_type is not SourceType.GITHUB_ISSUE:
            return super()._sections(document)
        matches = list(_COMMENT_RE.finditer(document.content))
        if not matches:
            return [
                TextSection(
                    0,
                    len(document.content),
                    [document.title, "Issue"],
                    metadata={"github_part": "issue"},
                )
            ]

        sections: list[TextSection] = []
        if document.content[: matches[0].start()].strip():
            sections.append(
                TextSection(
                    0,
                    matches[0].start(),
                    [document.title, "Issue"],
                    metadata={"github_part": "issue"},
                )
            )
        comments = document.source_metadata.get("comments", [])
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(document.content)
            source_comment = (
                comments[index]
                if isinstance(comments, list)
                and index < len(comments)
                and isinstance(comments[index], dict)
                else {}
            )
            sections.append(
                TextSection(
                    match.start(),
                    end,
                    [document.title, f"Comment by {match.group('author').strip()}"],
                    metadata={
                        "github_part": "comment",
                        "comment_index": index,
                        "comment_author": match.group("author").strip(),
                        "comment_time_raw": match.group("time")
                        or source_comment.get("created_at"),
                        "comment_url_raw": source_comment.get("html_url"),
                        "author_association": source_comment.get("author_association"),
                    },
                )
            )
        return sections

    def _section_metadata(
        self,
        document: StructuredDocument,
        section: TextSection,
        content: str,
        start: int,
        end: int,
    ) -> dict[str, Any]:
        metadata = super()._section_metadata(document, section, content, start, end)
        source = document.source_metadata
        kind = str(
            source.get("github_kind")
            or (
                "github_issue"
                if document.source_type is SourceType.GITHUB_ISSUE
                else "github_release"
            )
        )
        part = metadata.pop("github_part", "release")
        comment_index = metadata.pop("comment_index", None)
        comment_author = metadata.pop("comment_author", None)
        comment_time = self._datetime_or_none(metadata.pop("comment_time_raw", None))
        comment_url = metadata.pop("comment_url_raw", None)
        association = metadata.pop("author_association", None)
        if part == "comment" and not comment_url:
            comment_url = document.url
        author_type = self._author_type(
            comment_author or document.author,
            association,
            source,
        )
        number = source.get("issue_number")
        if number is None:
            number = source.get("release_id")
        metadata.update(
            {
                "section_type": f"github_{part}",
                "repository": source.get("repository"),
                "github_kind": kind,
                "github_number": number,
                "github_labels": source.get("labels") or [],
                "github_state": source.get("issue_state") or source.get("state"),
                "author_type": author_type,
                "comment_url": comment_url,
                "comment_time": comment_time,
            }
        )
        if comment_index is not None:
            metadata["source_metadata"] = {
                **source,
                "comment_index": comment_index,
                "comment_author": comment_author,
            }
        return metadata

    @staticmethod
    def _datetime_or_none(value: Any) -> datetime | None:
        if not value:
            return None
        if isinstance(value, datetime):
            return value
        try:
            return date_parser.parse(str(value))
        except (TypeError, ValueError, OverflowError):
            return None

    @staticmethod
    def _author_type(
        author: str | None,
        association: Any,
        source_metadata: dict[str, Any],
    ) -> str | None:
        if not author and not association:
            return None
        folded_association = str(association or "").casefold()
        if folded_association in {"owner", "member", "collaborator"}:
            return "maintainer"
        folded = str(author or "").casefold()
        maintainers = {
            str(value).casefold()
            for value in source_metadata.get("maintainers", [])
        }
        if folded in maintainers or any(
            marker in folded for marker in ("maintainer", "owner", "admin")
        ):
            return "maintainer"
        if folded.endswith("[bot]") or folded.endswith("-bot") or folded == "bot":
            return "bot"
        return "community"


__all__ = ["GitHubChunker"]
