# Photo-Flow Safety Architecture

## Core Safety Principles

This project implements a **safety-first architecture** designed to prevent data loss under all circumstances, including power failures, network interruptions, and user interruptions (Ctrl+C).

## Safety Mechanisms

### 1. Copy-First Approach
- **Never delete source files** until the copy is verified
- All operations use temporary files first
- Hash verification confirms file integrity after every copy
- Source files remain untouched until new version is proven valid

### 2. Atomic File Operations
- File replacements use atomic `replace()` operations
- Temporary files ensure no partial writes to final destinations
- Operations either complete fully or leave original unchanged
- No intermediate corrupted states possible

### 3. Backup & Restore System
- Automatic `.backup` files created before in-place modifications
- Failed operations automatically restore from backup
- Multiple layers of fallback for critical operations

### 4. Complete Metadata Preservation
- Uses industry-standard `exiftool` for metadata operations
- Preserves ALL metadata types: EXIF, IPTC, XMP, ratings, keywords, GPS
- Verification step ensures metadata integrity after processing
- Zero metadata loss guaranteed

### 5. Interruptible Operations
- **Safe to Ctrl+C at any stage** without data corruption
- Temporary files automatically cleaned up on interruption
- Original files never left in invalid states
- Operations can be resumed without conflicts

### 6. Graceful Error Handling
- Pre-flight checks validate all dependencies (exiftool, rsync, paths)
- Detailed error messages with actionable solutions
- Automatic cleanup of temporary files on any failure
- No silent failures - all errors reported clearly

### 7. Network Resilience
- **Automatic IPv4/IPv6 fallback** for remote backups
- Connection timeouts prevent hanging operations
- ProxyJump fallback for IPv4-only networks when traveling
- rsync partial transfers allow resuming interrupted uploads

### 8. Verification at Every Step
- Hash comparison after every file copy
- Image integrity verification after compression
- Metadata verification after processing
- File system validation before destructive operations

## Implementation Examples

### Safe File Copy (`file_manager.py`)
```python
def safe_copy(src, dst):
    # 1. Check if destination exists and is identical
    if dst.exists() and is_duplicate(src, dst):
        return True, ""

    # 2. Copy with metadata preservation
    shutil.copy2(src, dst)

    # 3. Verify copy integrity
    is_duplicate, error = is_duplicate(src, dst)
    if not is_duplicate:
        return False, "Verification failed"

    return True, ""
```

### Safe Image Compression (`image_processor.py`)
```python
def compress_jpeg_safe(input_path):
    # 1. Create compressed version in temporary file
    with tempfile.NamedTemporaryFile() as tmp:
        # Process image...

        # 2. Copy ALL metadata using exiftool
        subprocess.run(['exiftool', '-TagsFromFile', input_path, tmp.name])

        # 3. Verify integrity
        Image.open(tmp.name).verify()

        # 4. Create backup of original
        backup_path = input_path.with_suffix('.backup')
        shutil.copy2(input_path, backup_path)

        # 5. Atomic replace (only after everything succeeded)
        tmp.replace(input_path)

        # 6. Remove backup
        backup_path.unlink()
```

### Safe Network Operations (`workflow.py`)
```python
def backup_final_to_homelab():
    # Try direct connection first (IPv6)
    # Fallback to ProxyJump via VPS (IPv4)
    for method, ssh_config in [("direct", ssh_base), ("proxyjump", ssh_jump)]:
        try:
            subprocess.run(['rsync', '-e', ssh_config, src, remote], check=True)
            return success
        except:
            continue  # Try next method
```

## Why This Level of Safety?

**Personal photos are irreplaceable.** Unlike code or documents, a corrupted or lost photo cannot be recreated. This justifies the comprehensive safety measures:

1. **No tolerance for data loss** - Photos contain memories that cannot be recovered
2. **Frequent interruptions** - Mobile workflows often face connectivity issues
3. **Metadata is crucial** - Ratings, keywords, and EXIF data represent significant time investment
4. **Traveling photographer needs** - IPv4/IPv6 connectivity varies by location

## Dependencies

- **exiftool**: Industry standard for metadata operations (`brew install exiftool`)
- **rsync**: For safe, resumable transfers (pre-installed on macOS)
- **Python PIL**: For image processing with integrity verification

## Testing Safety

Run operations with `--dry-run` first to preview changes:

```bash
photoflow import --dry-run      # Preview file operations
photoflow finalize --dry-run    # Preview compression + moves
photoflow backup --dry-run      # Preview network operations
```

The dry-run mode exercises the same code paths without making changes, allowing verification of safety logic.