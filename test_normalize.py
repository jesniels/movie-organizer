import re

def normalize_name(name: str) -> str:
    normalized: str = name
    # Remove common year patterns like " (2019)" or " 2019"
    normalized = re.sub(r"\(\d{4}\)", "", normalized)
    normalized = re.sub(r"\b\d{4}\b", "", normalized)
    # Remove quality indicators like "(1080p HD)" or "1080p"
    normalized = re.sub(r"\(?\d+p\s*hd?\)?", "", normalized, flags=re.IGNORECASE)
    # Remove leading sequence numbers like "01 ", "02 ", "03 " (zero-padded or high numbers like 10+)
    # Only match 2-digit numbers (01-99) to avoid stripping single digit movie titles like "2 Guns"
    normalized = re.sub(r"^(\d{2})\s+", "", normalized)
    # Now convert to lowercase
    normalized = normalized.lower()
    # Keep only alphanumeric characters and spaces
    normalized = re.sub(r"[^a-z0-9 ]+", " ", normalized)
    # Collapse whitespace
    normalized = " ".join(normalized.split())
    return normalized

print('iTunes "2 Guns":', repr(normalize_name('2 Guns')))
print('File "01 2 Guns (1080p HD)":', repr(normalize_name('01 2 Guns (1080p HD)')))
print('Match:', normalize_name('2 Guns') == normalize_name('01 2 Guns (1080p HD)'))

print('\niTunes "Guardians of the Galaxy Vol. 2":', repr(normalize_name('Guardians of the Galaxy Vol. 2')))
print('File "02 Guardians of the Galaxy Vol. 2 (1080p HD)":', repr(normalize_name('02 Guardians of the Galaxy Vol. 2 (1080p HD)')))
print('Match:', normalize_name('Guardians of the Galaxy Vol. 2') == normalize_name('02 Guardians of the Galaxy Vol. 2 (1080p HD)'))

print('\niTunes "The Hunger Games_ Mockingjay - Part 2":', repr(normalize_name('The Hunger Games_ Mockingjay - Part 2')))
print('File "04 The Hunger Games_ Mockingjay - Part 2 (1080p HD)":', repr(normalize_name('04 The Hunger Games_ Mockingjay - Part 2 (1080p HD)')))
print('Match:', normalize_name('The Hunger Games_ Mockingjay - Part 2') == normalize_name('04 The Hunger Games_ Mockingjay - Part 2 (1080p HD)'))
