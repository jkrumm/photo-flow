import { GluegunCommand } from 'gluegun'
import { ensureProjectRoot, loadConfig } from '../services/configLoader'
import { DirectoryStatus, PhotoFlowState } from '../types/state'
import { Config } from '../types/config'

/**
 * Base command class that ensures commands run from the project root.
 * All photo-flow commands should extend this class.
 */
export abstract class BaseCommand {
  abstract name: string
  abstract description: string
  hidden = true

  // Commands can override these to specify what state they require
  protected requiresSdCard = false
  protected requiresSmb = false

  /**
   * Get emoji and label for each directory type
   */
  private getDirectoryInfo(type: DirectoryStatus['type']): {
    emoji: string
    label: string
  } {
    switch (type) {
      case 'camera':
        return { emoji: '📸', label: 'Camera' }
      case 'staging':
        return { emoji: '🔄', label: 'Staging' }
      case 'archive':
        return { emoji: '📦', label: 'Archive' }
      case 'immich':
        return { emoji: '🖼️', label: 'Immich' }
    }
  }

  /**
   * Validates the current state of external dependencies
   */
  private async validateState(toolbox: any): Promise<PhotoFlowState> {
    const { filesystem, system } = toolbox
    const state: PhotoFlowState = {
      directories: [],
      smbConnected: false,
      config: null,
    }

    // Load config first as we need it for validation
    state.config = await loadConfig(toolbox)
    if (!state.config) {
      throw new Error('Failed to load configuration')
    }

    // Check all directories
    const directoryTypes: DirectoryStatus['type'][] = [
      'camera',
      'staging',
      'archive',
      'immich',
    ]
    for (const type of directoryTypes) {
      const path = state.config.paths[type]
      const exists = await filesystem.existsAsync(path)
      state.directories.push({ type, path, exists })
    }

    // Check SMB connections if configured
    if (state.config.network.shares && state.config.network.shares.length > 0) {
      const mountedVolumes = await system.run('ls -la /Volumes')
      state.smbConnected = state.config.network.shares.every((share) =>
        mountedVolumes.includes(share.name)
      )

      // Validate required SMB state
      if (this.requiresSmb && !state.smbConnected) {
        const disconnectedShares = state.config.network.shares
          .filter((share) => !mountedVolumes.includes(share.name))
          .map((share) => share.name)
          .join(', ')
        throw new Error(
          `Required SMB shares not mounted: ${disconnectedShares}`
        )
      }
    }

    // Validate required camera state
    const cameraStatus = state.directories.find((d) => d.type === 'camera')
    if (this.requiresSdCard && cameraStatus && !cameraStatus.exists) {
      throw new Error(`Camera SD card not found at ${cameraStatus.path}`)
    }

    // Print status
    this.printStatus(toolbox, state)

    return state
  }

  /**
   * Print a nice status overview
   */
  private printStatus(toolbox: any, state: PhotoFlowState) {
    const { print, filesystem } = toolbox
    const { checkmark, colors } = print

    print.info('\nSystem Status:')

    // Print directory statuses
    state.directories.forEach((dir) => {
      const { emoji, label } = this.getDirectoryInfo(dir.type)
      const status = dir.exists
        ? colors.green(`${checkmark} Connected`)
        : colors.red('✗ Not Found')
      // Add extra space after 🖼️ emoji to compensate for its double-width
      const emojiPadding = emoji === '🖼️' ? ' ' : ''
      print.info(`  ${emoji}${emojiPadding}  ${label.padEnd(8)} ${status}`)
      if (!dir.exists) {
        print.info(`     ${colors.dim(dir.path)}`)
      }
    })

    // Print SMB status if configured
    if (state.config?.network.shares?.length) {
      const smbStatus = state.smbConnected
        ? colors.green(`${checkmark} Connected`)
        : colors.red('✗ Disconnected')
      print.info(`  🔌  SMB      ${smbStatus}`)

      // Show individual share status if disconnected
      if (!state.smbConnected && state.config.network.shares) {
        const mountedVolumes = filesystem.list('/Volumes') || []
        state.config.network.shares.forEach((share) => {
          const shareStatus = mountedVolumes.includes(share.name)
            ? colors.green(`${checkmark} Mounted`)
            : colors.red('✗ Not Mounted')
          print.info(`     ${colors.dim(share.name.padEnd(10))} ${shareStatus}`)
        })
      }
    }

    print.info('') // Empty line for spacing
  }

  /**
   * Creates a Gluegun command that ensures it runs from the project root
   */
  createCommand(): GluegunCommand {
    return {
      name: this.name,
      description: this.description,
      hidden: this.hidden,
      run: async (toolbox: any): Promise<void> => {
        try {
          // Ensure we're in the project root before running the command
          await ensureProjectRoot(toolbox)

          // Validate system state
          const state = await this.validateState(toolbox)

          // Run the actual command implementation with state
          await this.runFromProjectRoot(toolbox, state)
        } catch (err: any) {
          toolbox.print.error(err.message)
          process.exit(1)
        }
      },
    }
  }

  /**
   * Implement this method in your command.
   * It will be called after ensuring we're in the project root and validating state.
   */
  abstract runFromProjectRoot(
    toolbox: any,
    state: PhotoFlowState
  ): Promise<void>
}
