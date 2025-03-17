import { BaseCommand } from '../lib/base-command'
import { PhotoFlowState } from '../types/state'

class DefaultCommand extends BaseCommand {
  name = 'photo-flow'
  description = 'Photo workflow management tool'
  hidden = false

  async runFromProjectRoot(toolbox: any, state: PhotoFlowState): Promise<void> {
    const { print } = toolbox
    print.info(
      print.colors.cyan('photo-flow - Photography Workflow Management')
    )
    print.info(
      print.colors.dim('Run `photo-flow --help` for available commands')
    )
  }
}

module.exports = new DefaultCommand().createCommand()
