import { build } from 'gluegun'
import { GluegunToolbox } from 'gluegun'

/**
 * Create the CLI and kick it off
 */
async function run(argv: string[] = []): Promise<GluegunToolbox> {
  // create a CLI runtime
  const cli = build('photo-flow')
    .src(__dirname)
    .plugins('./node_modules', { matching: 'photo-flow-*', hidden: true })
    .help() // provides default for help, h, --help, -h
    .version() // provides default for version, v, --version, -v
    .create()
  // enable the following method if you'd like to skip loading one of these core extensions
  // this can improve performance if they're not necessary for your project:
  // .exclude(['meta', 'strings', 'print', 'filesystem', 'semver', 'system', 'prompt', 'http', 'template', 'patching', 'package-manager'])
  // and run it
  const toolbox = await cli.run(argv)

  // send it back (for testing, mostly)
  return toolbox
}

module.exports = { run }
