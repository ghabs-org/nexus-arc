import os
import shutil
import subprocess
import tempfile
from typing import Any, Callable


def transcribe_with_copilot_cli(
    *,
    check_tool_available: Callable[[Any], bool],
    copilot_provider: Any,
    copilot_cli_path: str,
    strip_cli_tool_output: Callable[[str], str],
    is_non_transcription_artifact: Callable[[str, str], bool],
    tool_unavailable_error: type[Exception],
    logger: Any,
    audio_file_path: str,
    timeout: int,
) -> str | None:
    if not check_tool_available(copilot_provider):
        raise tool_unavailable_error("Copilot CLI not available")
    if not os.path.exists(audio_file_path):
        raise ValueError(f"Audio file not found: {audio_file_path}")

    logger.info("🎧 Transcribing with Copilot (fallback): %s", audio_file_path)
    try:
        with tempfile.TemporaryDirectory(prefix="nexus_audio_") as temp_dir:
            audio_basename = os.path.basename(audio_file_path)
            staged_audio_path = os.path.join(temp_dir, audio_basename)
            shutil.copy2(audio_file_path, staged_audio_path)
            prompt = (
                "You are a speech-to-text (STT) transcriber. "
                "Transcribe only the spoken words from the attached audio file.\n"
                "Output rules:\n"
                "- Return ONLY the transcript text\n"
                "- Do NOT summarize, explain, or describe the file\n"
                "- Do NOT include labels like 'File:' or any metadata\n"
                "- Do NOT include apologies or capability statements\n"
                f"Audio file name: {audio_basename}"
            )
            result = subprocess.run(
                [copilot_cli_path, "-p", prompt, "--add-dir", temp_dir],
                capture_output=True,
                text=True,
                timeout=timeout,
            )

        if result.returncode != 0:
            raise Exception(f"Copilot error: {result.stderr}")
        text = strip_cli_tool_output(result.stdout or "").strip()
        if text:
            if is_non_transcription_artifact(text, audio_file_path):
                raise Exception("Copilot returned non-transcription content")
            return text
        raise Exception("Copilot returned empty transcription")
    except subprocess.TimeoutExpired as exc:
        raise Exception(f"Copilot transcription timed out (>{timeout}s)") from exc
