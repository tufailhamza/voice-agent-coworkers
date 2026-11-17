import asyncio
import json
import logging
import re
from dataclasses import replace
from datetime import datetime, timezone
from typing import Dict, List, Sequence, Tuple

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from .config import Config
from .models import Transcript

logger = logging.getLogger(__name__)

MAX_MERGE_GAP_SECONDS = 3.0
MAX_MERGED_SPAN_SECONDS = 30.0
MAX_SEGMENT_CHARACTERS = 400
MAX_SEGMENT_WORDS = 70
MAX_BATCH_SEGMENTS = 50
MAX_BATCH_CHARACTERS = 20_000
CONTEXT_SEGMENTS = 3
MAX_CORRECTION_CONCURRENCY = 3


class TranscriptCorrection(BaseModel):
    segment_id: str = Field(min_length=1)
    corrected_text: str = Field(min_length=1)


class TranscriptCorrectionBatch(BaseModel):
    corrections: List[TranscriptCorrection]


class TranscriptFinalizer:
    """Merge transcript fragments and safely correct their text."""

    def __init__(self, config: Config):
        self._api_key = config.ai.openai_api_key
        correction_llm = ChatOpenAI(
            model="gpt-4.1",
            temperature=0,
            api_key=self._api_key,
            timeout=90,
            max_retries=2,
        )
        self._correction_llm = correction_llm.with_structured_output(
            TranscriptCorrectionBatch,
            method="json_schema",
        )

    async def finalize(self, transcripts: Sequence[Transcript]) -> List[Transcript]:
        """Return timestamp-ordered, merged, and text-corrected transcripts."""
        merged = self.merge_transcripts(transcripts)
        if not merged or not self._api_key:
            if merged and not self._api_key:
                logger.warning(
                    "Transcript correction skipped because the OpenAI API key is missing"
                )
            return merged

        batches = self._build_batches(merged)
        semaphore = asyncio.Semaphore(MAX_CORRECTION_CONCURRENCY)
        speaker_labels = self._speaker_labels(merged)

        async def correct_batch(
            batch_index: int, start: int, end: int
        ) -> Dict[int, str]:
            async with semaphore:
                return await self._correct_batch(
                    merged,
                    batch_index=batch_index,
                    start=start,
                    end=end,
                    speaker_labels=speaker_labels,
                )

        results = await asyncio.gather(
            *[
                correct_batch(batch_index, start, end)
                for batch_index, (start, end) in enumerate(batches)
            ],
            return_exceptions=True,
        )

        corrected_text_by_index: Dict[int, str] = {}
        for batch_index, result in enumerate(results):
            if isinstance(result, Exception):
                logger.warning(
                    "Transcript correction batch %s failed; using merged text: %s",
                    batch_index,
                    result,
                )
                continue
            corrected_text_by_index.update(result)

        # Rebuild from the original objects so the LLM can only affect text_content.
        return [
            replace(
                transcript,
                text_content=corrected_text_by_index.get(
                    index, transcript.text_content
                ),
            )
            for index, transcript in enumerate(merged)
        ]

    @classmethod
    def merge_transcripts(cls, transcripts: Sequence[Transcript]) -> List[Transcript]:
        """Merge adjacent same-speaker fragments without creating oversized entries."""
        ordered = [
            transcript
            for _, transcript in sorted(
                enumerate(transcripts),
                key=lambda item: cls._sort_key(item[1], item[0]),
            )
            if transcript.text_content and transcript.text_content.strip()
        ]
        if not ordered:
            return []

        merged: List[Transcript] = []
        current = replace(ordered[0], text_content=ordered[0].text_content.strip())
        first_timestamp = cls._timestamp_value(current.timestamp)
        last_timestamp = first_timestamp

        for transcript in ordered[1:]:
            clean_text = transcript.text_content.strip()
            timestamp = cls._timestamp_value(transcript.timestamp)
            combined_text = f"{current.text_content.rstrip()} {clean_text.lstrip()}"
            gap = (
                timestamp - last_timestamp
                if timestamp is not None and last_timestamp is not None
                else None
            )
            span = (
                timestamp - first_timestamp
                if timestamp is not None and first_timestamp is not None
                else None
            )

            can_merge = (
                transcript.speaker_name == current.speaker_name
                and transcript.participant_sid == current.participant_sid
                and gap is not None
                and 0 <= gap <= MAX_MERGE_GAP_SECONDS
                and span is not None
                and span <= MAX_MERGED_SPAN_SECONDS
                and len(combined_text) <= MAX_SEGMENT_CHARACTERS
                and len(combined_text.split()) <= MAX_SEGMENT_WORDS
            )

            if can_merge:
                current = replace(current, text_content=combined_text)
                last_timestamp = timestamp
                continue

            merged.extend(cls._split_oversized(current))
            current = replace(transcript, text_content=clean_text)
            first_timestamp = timestamp
            last_timestamp = timestamp

        merged.extend(cls._split_oversized(current))
        return merged

    async def _correct_batch(
        self,
        transcripts: Sequence[Transcript],
        *,
        batch_index: int,
        start: int,
        end: int,
        speaker_labels: Dict[str, str],
    ) -> Dict[int, str]:
        target_id_to_index = {
            f"batch_{batch_index}_segment_{local_index}": transcript_index
            for local_index, transcript_index in enumerate(range(start, end))
        }
        before_start = max(0, start - CONTEXT_SEGMENTS)
        after_end = min(len(transcripts), end + CONTEXT_SEGMENTS)

        def context_item(transcript: Transcript) -> dict:
            return {
                "speaker": speaker_labels[transcript.speaker_name],
                "text": transcript.text_content,
            }

        targets = [
            {
                "segmentId": segment_id,
                "speaker": speaker_labels[transcripts[index].speaker_name],
                "text": transcripts[index].text_content,
            }
            for segment_id, index in target_id_to_index.items()
        ]
        payload = {
            "contextBefore": [
                context_item(transcripts[index]) for index in range(before_start, start)
            ],
            "targets": targets,
            "contextAfter": [
                context_item(transcripts[index]) for index in range(end, after_end)
            ],
        }

        system_prompt = """You are a conservative meeting-transcript correction layer.

Correct only the target segments. The surrounding segments are read-only context.
- Fix punctuation, capitalization, spelling, and obvious speech-to-text mistakes.
- Use conversation context to resolve clear transcription errors.
- Preserve the original meaning, language, tone, and level of formality.
- Some text is a non-English language transliterated into Roman/Latin script. Never
  translate it into English and never convert it into another script. For example,
  "mujhe headache hai" must remain Romanized and must not become "I have a headache".
- The company/product name is "QLU". Correct variants such as "clue.ai", "Q L U",
  and phonetic "clue" to "QLU" only when context refers to the company, product,
  platform, application, website, or workplace. Preserve ordinary uses such as
  "I have no clue".
- Do not summarize, add facts, remove meaningful content, combine segments, or split
  segments.
- Return every target segment exactly once with the same segmentId. Return no context
  segments and no commentary."""

        try:
            response = await self._correction_llm.ainvoke(
                [
                    SystemMessage(content=system_prompt),
                    HumanMessage(
                        content=(
                            "Correct the target transcript segments in this JSON data:\n"
                            + json.dumps(payload, ensure_ascii=False)
                        )
                    ),
                ]
            )
            corrections = (
                response.corrections
                if isinstance(response, TranscriptCorrectionBatch)
                else TranscriptCorrectionBatch.model_validate(response).corrections
            )
            return self._validate_corrections(
                transcripts, target_id_to_index, corrections, batch_index
            )
        except Exception as exc:
            logger.warning(
                "Transcript correction batch %s failed; using merged text: %s",
                batch_index,
                exc,
            )
            return {}

    @staticmethod
    def _validate_corrections(
        transcripts: Sequence[Transcript],
        target_id_to_index: Dict[str, int],
        corrections: Sequence[TranscriptCorrection],
        batch_index: int,
    ) -> Dict[int, str]:
        returned_ids = [correction.segment_id for correction in corrections]
        expected_ids = set(target_id_to_index)
        if (
            len(returned_ids) != len(set(returned_ids))
            or set(returned_ids) != expected_ids
        ):
            logger.warning(
                "Transcript correction batch %s returned missing, duplicate, or unknown IDs; using merged text",
                batch_index,
            )
            return {}

        validated: Dict[int, str] = {}
        for correction in corrections:
            index = target_id_to_index[correction.segment_id]
            original = transcripts[index].text_content
            corrected = correction.corrected_text.strip()
            maximum_length = max(len(original) * 3, len(original) + 150)
            if not corrected or len(corrected) > maximum_length:
                logger.warning(
                    "Transcript correction rejected for %s due to invalid length",
                    correction.segment_id,
                )
                validated[index] = original
                continue
            validated[index] = corrected
        return validated

    @staticmethod
    def _build_batches(transcripts: Sequence[Transcript]) -> List[Tuple[int, int]]:
        batches: List[Tuple[int, int]] = []
        start = 0
        while start < len(transcripts):
            end = start
            character_count = 0
            while end < len(transcripts) and end - start < MAX_BATCH_SEGMENTS:
                next_length = len(transcripts[end].text_content)
                if end > start and character_count + next_length > MAX_BATCH_CHARACTERS:
                    break
                character_count += next_length
                end += 1
            batches.append((start, end))
            start = end
        return batches

    @staticmethod
    def _speaker_labels(transcripts: Sequence[Transcript]) -> Dict[str, str]:
        labels: Dict[str, str] = {}
        for transcript in transcripts:
            if transcript.speaker_name not in labels:
                labels[transcript.speaker_name] = f"Speaker {len(labels) + 1}"
        return labels

    @staticmethod
    def _timestamp_value(timestamp: datetime | None) -> float | None:
        if timestamp is None:
            return None
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        return timestamp.timestamp()

    @classmethod
    def _sort_key(
        cls, transcript: Transcript, original_index: int
    ) -> Tuple[int, float, int]:
        timestamp = cls._timestamp_value(transcript.timestamp)
        if timestamp is None:
            return (1, 0.0, original_index)
        return (0, timestamp, original_index)

    @classmethod
    def _split_oversized(cls, transcript: Transcript) -> List[Transcript]:
        text = transcript.text_content.strip()
        if (
            len(text) <= MAX_SEGMENT_CHARACTERS
            and len(text.split()) <= MAX_SEGMENT_WORDS
        ):
            return [replace(transcript, text_content=text)]

        sentences = [
            sentence.strip()
            for sentence in re.findall(
                r".+?(?:[.!?]+(?=\s|$)|$)", text, flags=re.DOTALL
            )
            if sentence.strip()
        ]
        if not sentences:
            sentences = [text]

        chunks: List[str] = []
        current = ""
        for sentence in sentences:
            for piece in cls._split_long_sentence(sentence):
                combined = f"{current} {piece}".strip()
                if current and (
                    len(combined) > MAX_SEGMENT_CHARACTERS
                    or len(combined.split()) > MAX_SEGMENT_WORDS
                ):
                    chunks.append(current)
                    current = piece
                else:
                    current = combined
        if current:
            chunks.append(current)

        return [replace(transcript, text_content=chunk) for chunk in chunks]

    @staticmethod
    def _split_long_sentence(sentence: str) -> List[str]:
        words = sentence.split()
        pieces: List[str] = []
        current_words: List[str] = []
        current_length = 0
        for word in words:
            added_length = len(word) + (1 if current_words else 0)
            if current_words and (
                len(current_words) >= MAX_SEGMENT_WORDS
                or current_length + added_length > MAX_SEGMENT_CHARACTERS
            ):
                pieces.append(" ".join(current_words))
                current_words = []
                current_length = 0
                added_length = len(word)
            current_words.append(word)
            current_length += added_length
        if current_words:
            pieces.append(" ".join(current_words))
        return pieces
