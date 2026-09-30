import re
from pathlib import Path

# Test cases with problematic filenames
test_filenames = [
    "xXx_ Return of Xander Cage (1080p HD).mp4",
    "Angreb på det Hvide Hus (Olympus Has.mp4",  # MISSING closing paren
    "Angel Has Fallen (2019).mp4",
    "Movie (1080p).mp4",
    "Movie (Full HD) (2020).mp4",
]

def parse_filename(filename):
    """Parse filename and extract clean name + year"""
    path = Path(filename)
    base_name = path.stem
    print(f"Original filename: {filename}")
    print(f"  Path.stem: {base_name}")
    
    # Fix unbalanced parentheses: count opening and closing FIRST
    open_count = base_name.count('(')
    close_count = base_name.count(')')
    
    if open_count != close_count:
        # Add missing closing parentheses
        base_name = base_name + ')' * (open_count - close_count)
        print(f"  Fixed unbalanced: {base_name}")
    
    # Extract year from any parentheses (4 digits)
    year_match = re.search(r'\((\d{4})\)', base_name)
    year = year_match.group(1) if year_match else None
    
    # Remove ONLY the year parentheses, keep everything else
    base_name_clean = re.sub(r'\s*\(\d{4}\)\s*', ' ', base_name).strip()
    # Remove any duplicate spaces
    base_name_clean = re.sub(r'\s+', ' ', base_name_clean)
    print(f"  Cleaned name: {base_name_clean}")
    
    # Final result - ONLY add year if it exists
    if year:
        final_result = f"{base_name_clean} ({year})"
    else:
        final_result = base_name_clean
    print(f"  Result: {final_result}")
    print()
    return base_name_clean, year

print("=== TESTING FILENAME PARSING ===\n")

for filename in test_filenames:
    parse_filename(filename)
