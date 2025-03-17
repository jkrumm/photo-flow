import { BaseCommand } from '../lib/base-command'
import { PhotoFlowState } from '../types/state'

class ConnectCommand extends BaseCommand {
  name = 'connect'
  description = 'Connect to configured SMB shares'
  hidden = false

  async runFromProjectRoot(toolbox: any, state: PhotoFlowState): Promise<void> {
    const { print, system } = toolbox
    const { colors } = print

    if (!state.config?.network?.shares?.length) {
      print.error(
        'No SMB shares configured. Run `photo-flow config init` to set up shares.'
      )
      return
    }

    // Check which shares need mounting
    const mountedVolumes = await system.run('ls -la /Volumes')
    const unmountedShares = state.config.network.shares.filter(
      (share) => !mountedVolumes.includes(share.name)
    )

    if (unmountedShares.length === 0) {
      print.success('\n🔌 All shares are already mounted!')
      return
    }

    print.info(colors.cyan('\n📡 Connecting to SMB shares...\n'))

    // Create AppleScript for mounting each share
    const spinner = print.spin('')
    let mountedCount = 0

    for (const share of unmountedShares) {
      const { smbServer, username } = state.config.network
      const url = `smb://${username}@${smbServer}/${share.sharePath}`

      spinner.text = `Mounting ${colors.cyan(share.name)}...`

      try {
        // Use AppleScript to mount the share
        // This integrates with macOS Keychain for password management
        const script = `
          try
            mount volume "${url}"
          on error errMsg
            return "Error: " & errMsg
          end try
        `
        const result = await system.run(`osascript -e '${script}'`)

        // Stop spinner before printing result
        spinner.stop()

        if (result && result.includes('Error:')) {
          print.error(
            `  ❌ Failed to mount ${colors.cyan(share.name)}: ${result}`
          )
        } else {
          mountedCount++
          print.success(
            `  ✓ Mounted ${colors.cyan(share.name)} → ${colors.dim(
              share.mountPoint
            )}`
          )
        }

        // Restart spinner for next share if there are more
        if (mountedCount < unmountedShares.length) {
          spinner.start()
        }
      } catch (error: any) {
        spinner.stop()
        print.error(
          `  ❌ Failed to mount ${colors.cyan(share.name)}: ${
            error.message || String(error)
          }`
        )
        if (mountedCount < unmountedShares.length - 1) {
          spinner.start()
        }
      }
    }

    // Ensure spinner is stopped
    spinner.stop()

    // Verify mounts
    const finalMountCheck = await system.run('ls -la /Volumes')
    const stillUnmounted = unmountedShares.filter(
      (share) => !finalMountCheck.includes(share.name)
    )

    if (stillUnmounted.length > 0) {
      print.error('\n❌ Some shares failed to mount:')
      stillUnmounted.forEach((share) => {
        print.error(
          `   • ${colors.cyan(share.name)} (${colors.dim(share.mountPoint)})`
        )
      })
      process.exit(1)
    }

    if (mountedCount === unmountedShares.length) {
      print.success('\n✨ All shares mounted successfully!')
    } else {
      print.info(
        `\n📊 Mounted ${mountedCount} of ${unmountedShares.length} shares`
      )
    }
  }
}

module.exports = new ConnectCommand().createCommand()
