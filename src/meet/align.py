"""Forced alignment пословных таймкодов через wav2vec2 (CTC), по мотивам whisperX.

Родные таймкоды whisper грубые (ставятся на уровне сегмента), из-за чего короткие
реплики на стыке спикеров уезжают соседу. Здесь текст сегмента выравнивается по
звуку отдельной фонемной CTC-моделью, давая точные пословные границы — их затем
использует split_by_speaker для привязки спикеров.

Модель `jonatasgrosman/wav2vec2-large-xlsr-53-russian` (Apache-2.0) — дефолт
whisperX для русского. Модуль самодостаточный, в основной пайплайн НЕ вплетён:
экспериментальный шаг, вызывается явно.
"""
from meet import asr
from meet.asr import Segment, Word

ALIGN_MODEL = "jonatasgrosman/wav2vec2-large-xlsr-53-russian"


def _tokenize(text: str, vocab: dict) -> list:
    """Символы слова → id словаря модели; отсутствующие (цифры, латиница,
    пунктуация) отбрасываем — выравнивать по ним нечем."""
    return [vocab[c] for c in text.strip().lower() if c in vocab]


def _regroup_words(words, token_counts, spans, seg_start, spf):
    """Символьные спаны (в кадрах эмиссии) → пословные Word с точным временем.
    Слово с 0 токенов (нечего выравнивать) остаётся с исходным таймкодом."""
    out = []
    cursor = 0
    for w, cnt in zip(words, token_counts):
        if cnt == 0:
            out.append(w)
            continue
        wspans = spans[cursor:cursor + cnt]
        cursor += cnt
        out.append(
            Word(seg_start + wspans[0].start * spf,
                 seg_start + wspans[-1].end * spf, w.text)
        )
    return out


def _load_align_model(device):
    from transformers import Wav2Vec2ForCTC, Wav2Vec2Processor

    processor = Wav2Vec2Processor.from_pretrained(ALIGN_MODEL)
    model = Wav2Vec2ForCTC.from_pretrained(ALIGN_MODEL).to(device).eval()
    return processor, model


def _align_device() -> str:
    """Устройство выравнивания — по тому же выбору, что у распознавания
    (`asr.device`): профиль CPU держит на CPU и wav2vec2, даже если карта есть.
    CUDA — только если её выбрали (или «auto» её нашёл) и torch её видит."""
    import torch

    if asr.resolve_device() == "cuda" and torch.cuda.is_available():
        return "cuda"
    return "cpu"


def align_segments(segments, wav_path, device=None):
    """Вернуть сегменты с точными пословными таймкодами (forced alignment).
    wav_path — mono 16 kHz wav (выход to_wav16k). Текст и границы сегментов
    не меняются, только word.start/word.end внутри них."""
    import wave

    import numpy as np
    import torch
    import torchaudio.functional as AF

    device = device or _align_device()
    with wave.open(str(wav_path), "rb") as wf:
        sr = wf.getframerate()
        audio = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
    audio = audio.astype(np.float32) / 32768.0

    processor, model = _load_align_model(device)
    vocab = processor.tokenizer.get_vocab()
    blank = processor.tokenizer.pad_token_id

    out_segments = []
    aligned = 0
    try:
        for seg in segments:
            if not seg.words:
                out_segments.append(seg)
                continue
            clip = audio[int(seg.start * sr):int(seg.end * sr)]
            counts = [len(_tokenize(w.text, vocab)) for w in seg.words]
            targets_flat = [t for w in seg.words for t in _tokenize(w.text, vocab)]
            if len(clip) < sr * 0.05 or not targets_flat:
                out_segments.append(seg)
                continue
            with torch.inference_mode():
                inp = processor(
                    clip, sampling_rate=sr, return_tensors="pt"
                ).input_values.to(device)
                logits = model(inp).logits  # (1, T, C)
            emission = torch.log_softmax(logits.float(), dim=-1).cpu()
            n_frames = emission.shape[1]
            if n_frames < len(targets_flat):
                out_segments.append(seg)  # кадров меньше числа токенов — не выровнять
                continue
            targets = torch.tensor([targets_flat], dtype=torch.int32)
            aligned_tokens, scores = AF.forced_align(emission, targets, blank=blank)
            spans = AF.merge_tokens(aligned_tokens[0], scores[0], blank=blank)
            spf = (len(clip) / n_frames) / sr
            new_words = _regroup_words(seg.words, counts, spans, seg.start, spf)
            out_segments.append(
                Segment(seg.start, seg.end, seg.text, seg.speaker, new_words,
                        seg.no_speech_prob, seg.avg_logprob,
                        uncertain=seg.uncertain)
            )
            aligned += 1
    finally:
        del model
        torch.cuda.empty_cache()
    print(f"forced alignment: выровнено {aligned}/{len(segments)} сегментов")
    return out_segments
