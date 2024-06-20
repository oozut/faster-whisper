import string

from functools import cached_property
from typing import List, Optional, Tuple, Union, Callable, NamedTuple

import tokenizers


class Word(NamedTuple):
    start: float
    end: float
    word: str
    probability: float


class Tokenizer:
    """Simple wrapper around a tokenizers.Tokenizer."""

    def __init__(
        self,
        tokenizer: tokenizers.Tokenizer,
        multilingual: bool,
        task: Optional[str] = None,
        language: Optional[str] = None,
        without_timestamps: bool = False,
        max_length: int = 448,
    ):
        self.tokenizer = tokenizer
        self.without_timestamps = without_timestamps
        self.max_length = max_length

        if multilingual:
            if task not in _TASKS:
                raise ValueError(
                    "'%s' is not a valid task (accepted tasks: %s)"
                    % (task, ", ".join(_TASKS))
                )

            if language not in _LANGUAGE_CODES:
                raise ValueError(
                    "'%s' is not a valid language code (accepted language codes: %s)"
                    % (language, ", ".join(_LANGUAGE_CODES))
                )

            self.task = self.tokenizer.token_to_id("<|%s|>" % task)
            self.language = self.tokenizer.token_to_id("<|%s|>" % language)
            self.language_code = language
        else:
            self.task = None
            self.language = None
            self.language_code = "en"

    @cached_property
    def transcribe(self) -> int:
        return self.tokenizer.token_to_id("<|transcribe|>")

    @cached_property
    def translate(self) -> int:
        return self.tokenizer.token_to_id("<|translate|>")

    @cached_property
    def sot(self) -> int:
        return self.tokenizer.token_to_id("<|startoftranscript|>")

    @cached_property
    def sot_lm(self) -> int:
        return self.tokenizer.token_to_id("<|startoflm|>")

    @cached_property
    def sot_prev(self) -> int:
        return self.tokenizer.token_to_id("<|startofprev|>")

    @cached_property
    def eot(self) -> int:
        return self.tokenizer.token_to_id("<|endoftext|>")

    @cached_property
    def no_timestamps(self) -> int:
        return self.tokenizer.token_to_id("<|notimestamps|>")

    @property
    def timestamp_begin(self) -> int:
        return self.no_timestamps + 1

    @property
    def sot_sequence(self) -> List[int]:
        sequence = [self.sot]

        if self.language is not None:
            sequence.append(self.language)

        if self.task is not None:
            sequence.append(self.task)

        return sequence
    
    def timestamp_to_token(self, timestamp: float) -> str:
        """Convert a timestamp to a token in the format <|x.xx|>"""
        if timestamp < 0 or timestamp > 30:
            raise ValueError("Timestamp must be between 0 and 30")
        timestamp = round(timestamp / 0.02) * 0.02
        return f"<|{timestamp:.2f}|>"

    def encode(self, text: Union[str, List[Word]]) -> List[int]:
        if isinstance(text, str):
            prefix_tokens = self.tokenizer.encode(
                " " + text.strip(), add_special_tokens=False
            ).ids
            if len(prefix_tokens) >= self.max_length // 2:
                prefix_tokens = prefix_tokens[: self.max_length // 2 - 1]
            if not self.without_timestamps:
                prefix_tokens.insert(0, self.timestamp_begin)
            return prefix_tokens
        if isinstance(text, list):
            if self.without_timestamps:
                return self.encode_without_timestamps(text)
            return self.encode_with_timestamps(text)
        
        raise ValueError("Text must be a string or a list of words")

    def encode_with_timestamps(self, words: List[Word]) -> List[int]:
        encode_words: Callable[[List[Word]], List[str]] = (
            lambda word: self.timestamp_to_token(word.start)
            + word.word
            + self.timestamp_to_token(word.end)
        )
        if self.language_code in {"zh", "ja", "th", "lo", "my", "yue"}:
            # These languages don't typically use spaces, so it is
            # difficult to split words
            return "".join(map(encode_words))
        return " ".join(map(encode_words))
    
    def encode_without_timestamps(self, words: List[Word]) -> List[int]:
        encode_words: Callable[[List[Word]], List[str]] = (
            lambda word:word.word
        )
        if self.language_code in {"zh", "ja", "th", "lo", "my", "yue"}:
            # These languages don't typically use spaces, so it is
            # difficult to split words
            return "".join(map(encode_words))
        return " ".join(map(encode_words))

    def decode(self, tokens: List[int]) -> str:
        text_tokens = [token for token in tokens if token < self.eot]
        return self.tokenizer.decode(text_tokens)

    def decode_with_timestamps(self, tokens: List[int]) -> str:
        outputs = [[]]

        for token in tokens:
            if token >= self.timestamp_begin:
                timestamp = f"<|{(token - self.timestamp_begin) * 0.02:.2f}|>"
                outputs.append(timestamp)
                outputs.append([])
            else:
                outputs[-1].append(token)

        return "".join(
            [s if isinstance(s, str) else self.tokenizer.decode(s) for s in outputs]
        )

    def split_to_word_tokens(
        self, tokens: List[int]
    ) -> Tuple[List[str], List[List[int]]]:
        if self.language_code in {"zh", "ja", "th", "lo", "my", "yue"}:
            # These languages don't typically use spaces, so it is difficult to split words
            # without morpheme analysis. Here, we instead split words at any
            # position where the tokens are decoded as valid unicode points
            return self.split_tokens_on_unicode(tokens)

        return self.split_tokens_on_spaces(tokens)

    def split_tokens_on_unicode(
        self, tokens: List[int]
    ) -> Tuple[List[str], List[List[int]]]:
        decoded_full = self.decode_with_timestamps(tokens)
        replacement_char = "\ufffd"

        words = []
        word_tokens = []
        current_tokens = []
        unicode_offset = 0

        for token in tokens:
            current_tokens.append(token)
            decoded = self.decode_with_timestamps(current_tokens)

            try:
                replacement_char_index = decoded.index(replacement_char)
                replacement_char_index += unicode_offset
            except ValueError:
                replacement_char_index = None

            if replacement_char_index is None or (
                replacement_char_index < len(decoded_full)
                and decoded_full[replacement_char_index] == replacement_char
            ):
                words.append(decoded)
                word_tokens.append(current_tokens)
                current_tokens = []
                unicode_offset += len(decoded)

        return words, word_tokens

    def split_tokens_on_spaces(
        self, tokens: List[int]
    ) -> Tuple[List[str], List[List[int]]]:
        subwords, subword_tokens_list = self.split_tokens_on_unicode(tokens)
        words = []
        word_tokens = []

        for subword, subword_tokens in zip(subwords, subword_tokens_list):
            special = subword_tokens[0] >= self.eot
            with_space = subword.startswith(" ")
            punctuation = subword.strip() in string.punctuation
            if special or with_space or punctuation or len(words) == 0:
                words.append(subword)
                word_tokens.append(subword_tokens)
            else:
                words[-1] = words[-1] + subword
                word_tokens[-1].extend(subword_tokens)

        return words, word_tokens


_TASKS = (
    "transcribe",
    "translate",
)

_LANGUAGE_CODES = (
    "af",
    "am",
    "ar",
    "as",
    "az",
    "ba",
    "be",
    "bg",
    "bn",
    "bo",
    "br",
    "bs",
    "ca",
    "cs",
    "cy",
    "da",
    "de",
    "el",
    "en",
    "es",
    "et",
    "eu",
    "fa",
    "fi",
    "fo",
    "fr",
    "gl",
    "gu",
    "ha",
    "haw",
    "he",
    "hi",
    "hr",
    "ht",
    "hu",
    "hy",
    "id",
    "is",
    "it",
    "ja",
    "jw",
    "ka",
    "kk",
    "km",
    "kn",
    "ko",
    "la",
    "lb",
    "ln",
    "lo",
    "lt",
    "lv",
    "mg",
    "mi",
    "mk",
    "ml",
    "mn",
    "mr",
    "ms",
    "mt",
    "my",
    "ne",
    "nl",
    "nn",
    "no",
    "oc",
    "pa",
    "pl",
    "ps",
    "pt",
    "ro",
    "ru",
    "sa",
    "sd",
    "si",
    "sk",
    "sl",
    "sn",
    "so",
    "sq",
    "sr",
    "su",
    "sv",
    "sw",
    "ta",
    "te",
    "tg",
    "th",
    "tk",
    "tl",
    "tr",
    "tt",
    "uk",
    "ur",
    "uz",
    "vi",
    "yi",
    "yo",
    "zh",
    "yue",
)
