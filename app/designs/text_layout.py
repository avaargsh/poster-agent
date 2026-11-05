"""Character-based wrapping shared by templates and editing commands."""


def split_text_chunks(text: str, max_chars: int) -> list[str]:
    """Preserve every character without creating whitespace-only text lines."""
    chunks: list[str] = []
    leading_space = ""
    for index in range(0, len(text), max_chars):
        chunk = text[index : index + max_chars]
        if chunk.strip():
            chunks.append(leading_space + chunk)
            leading_space = ""
        elif chunks:
            chunks[-1] += chunk
        else:
            leading_space += chunk
    return chunks
