import { GluegunCommand } from 'gluegun'
import { ensureProjectRoot } from '../services/configLoader'

/**
 * Base command class that ensures commands run from the project root.
 * All photo-flow commands should extend this class.
 */
export abstract class BaseCommand {
  abstract name: string
  abstract description: string
  hidden = true

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

          // Run the actual command implementation
          await this.runFromProjectRoot(toolbox)
        } catch (err: any) {
          toolbox.print.error(err.message)
          process.exit(1)
        }
      },
    }
  }

  /**
   * Implement this method in your command.
   * It will be called after ensuring we're in the project root.
   */
  abstract runFromProjectRoot(toolbox: any): Promise<void>
}
