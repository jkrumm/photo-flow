# Command Reference for photo-flow

## Project Location

photo-flow automatically manages your project location, allowing you to run commands from anywhere on your system:

- First run: You'll be prompted to specify your photo-flow project location
- Subsequent runs: Automatically changes to your project directory
- Project path is stored in `~/.photo-flow-global.json`

## Configuration Commands

### `photo-flow config list [--debug]`

Lists all current configuration settings in a table format.

- Sensitive information like passwords are masked
- Use `--debug` to see detailed configuration loading process

```bash
photo-flow config list
photo-flow config list --debug
```

### `photo-flow config get <key>`

Retrieves a specific configuration value using dot notation.

```bash
photo-flow config get paths.camera
photo-flow config get network.shares
```

### `photo-flow config set <key>`

Sets a specific configuration value. Will prompt for the new value.

```bash
photo-flow config set paths.staging
photo-flow config set network.smbServer
```

### `photo-flow config init`

Interactive configuration wizard that helps you set up:

- Photo directory paths (camera, staging, archive, immich)
- Network settings for SMB shares
  - Server address
  - Authentication credentials
  - Share configurations

```bash
photo-flow config init
```

## Configuration System

photo-flow uses a three-level configuration system:

1. **Base Configuration** (`.photo-flowrc.js`)

   - Version controlled
   - Defines project defaults
   - Located in project root

2. **User Configuration** (`~/.photo-flow.json`)

   - User-specific settings
   - Not version controlled
   - Located in home directory

3. **Local Configuration** (`.photo-flow.local.json`)
   - Local environment overrides
   - Not version controlled (gitignored)
   - Located in project root

### Configuration Structure

#### Paths Configuration

```json
{
  "paths": {
    "camera": "/Volumes/Fuji X-T4/DCIM",
    "staging": "/Volumes/SSD/Photos/Staging",
    "archive": "/Volumes/HDD/Photos/Archive",
    "immich": "/Volumes/SSD/Photos/Immich"
  }
}
```

#### Network Configuration

```json
{
  "network": {
    "smbServer": "samba.jkrumm.dev",
    "username": "your_username",
    "password": "********",
    "shares": [
      {
        "name": "HDD",
        "sharePath": "HDD",
        "mountPoint": "/Volumes/HDD"
      },
      {
        "name": "SSD",
        "sharePath": "SSD",
        "mountPoint": "/Volumes/SSD"
      }
    ]
  }
}
```

## SMB Mounting

photo-flow uses macOS's built-in SMB mounting via AppleScript for a reliable and user-friendly experience:

```applescript
mount volume "smb://user@server/share"
```

Benefits:

- Integrates with macOS Keychain for secure credential storage
- Provides native Finder integration
- Handles reconnection automatically
- Supports multiple shares from the same server

## Debug Mode

Add `--debug` to any command to see detailed information about:

- Project root detection
- Configuration file loading
- Configuration merging process
- Final configuration values

```bash
photo-flow --debug config list
photo-flow --debug config get paths.camera
```

### `photo-flow connect`

Connects to configured SMB shares using macOS's native mounting system.

```bash
photo-flow connect
```

Features:

- Uses native macOS SMB mounting via AppleScript
- Integrates with Keychain for secure credential storage
- Mounts all configured shares from `.photo-flowrc.js`
- Shows real-time progress with status indicators
- Verifies successful mounting of all shares

Example output:

```
System Status:
  📸  Camera   ✓ Connected
  🔄  Staging  ✗ Not Found
  📦  Archive  ✗ Not Found
  🖼️  Immich   ✓ Connected
  🔌  SMB      ✗ Disconnected
     HDD        ✗ Not Mounted
     SSD        ✗ Not Mounted

📡 Connecting to SMB shares...

  ✓ Mounted HDD → /Volumes/HDD
  ✓ Mounted SSD → /Volumes/SSD

✨ All shares mounted successfully!
```

The command will:

1. Check current mount status of all configured shares
2. Skip already mounted shares
3. Mount each unmounted share using AppleScript
4. Verify successful mounting
5. Show detailed status for any failed mounts

If any mounts fail, the command will:

- Show which shares failed to mount
- Display the mount point that was attempted
- Exit with status code 1
