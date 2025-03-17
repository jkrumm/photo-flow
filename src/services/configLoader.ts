import { GluegunToolbox } from 'gluegun'
import { Config } from '../types/config'
import path from 'path'
import os from 'os'

/**
 * Gets the path to the global config file in user's home directory
 */
function getGlobalConfigPath(): string {
  return path.join(os.homedir(), '.photo-flow-global.json')
}

/**
 * Loads or prompts for the project root path
 */
export async function getProjectRoot(toolbox: GluegunToolbox): Promise<string> {
  const { filesystem, prompt, print, parameters } = toolbox
  const debug = parameters?.options?.debug
  const globalConfigPath = getGlobalConfigPath()

  // Try to load existing project path
  if (await filesystem.existsAsync(globalConfigPath)) {
    try {
      const globalConfig = await filesystem.readAsync(globalConfigPath, 'json')
      if (globalConfig?.projectPath) {
        // Verify the path still exists and contains .photo-flowrc.js
        if (
          await filesystem.existsAsync(
            path.join(globalConfig.projectPath, '.photo-flowrc.js')
          )
        ) {
          if (debug)
            print.info(
              `Using configured project path: ${globalConfig.projectPath}`
            )
          return globalConfig.projectPath
        }
      }
    } catch (err) {
      if (debug) print.error(`Error loading global config: ${err}`)
    }
  }

  // If we're in a project directory, use that and save it
  let currentDir = process.cwd()
  while (currentDir !== '/') {
    if (
      await filesystem.existsAsync(path.join(currentDir, '.photo-flowrc.js'))
    ) {
      await filesystem.writeAsync(globalConfigPath, { projectPath: currentDir })
      if (debug)
        print.info(`Saved current directory as project path: ${currentDir}`)
      return currentDir
    }
    currentDir = path.dirname(currentDir)
  }

  // Need to prompt for project path
  print.info('No photo-flow project configured.')
  const { projectPath } = await prompt.ask([
    {
      type: 'input',
      name: 'projectPath',
      message: 'Enter the path to your photo-flow project:',
      initial: path.join(os.homedir(), 'SourceRoot/photo-flow'),
    },
  ])

  // Verify the path
  if (
    !(await filesystem.existsAsync(path.join(projectPath, '.photo-flowrc.js')))
  ) {
    throw new Error(
      `No .photo-flowrc.js found in ${projectPath}.\n` +
        'Please make sure you enter the correct path to your photo-flow project.'
    )
  }

  // Save the path
  await filesystem.writeAsync(globalConfigPath, { projectPath })
  if (debug) print.info(`Saved configured project path: ${projectPath}`)

  return projectPath
}

/**
 * Finds the project root directory and changes the current working directory to it.
 * Returns the project root path.
 */
export async function ensureProjectRoot(
  toolbox: GluegunToolbox
): Promise<string> {
  const projectRoot = await getProjectRoot(toolbox)
  process.chdir(projectRoot)
  return projectRoot
}

/**
 * Loads configuration using a two-level system:
 * 1. Default config from .photo-flowrc.js in the project root
 * 2. Local overrides from .photo-flow.local.json in the project root
 */
export async function loadConfig(toolbox: GluegunToolbox): Promise<Config> {
  const { filesystem, parameters } = toolbox
  const debug = parameters?.options?.debug

  try {
    // Ensure we're in the project root
    const projectRoot = await ensureProjectRoot(toolbox)

    // Load base config from .photo-flowrc.js
    let baseConfig = getDefaultConfig()
    const rcPath = filesystem.path(projectRoot, '.photo-flowrc.js')

    try {
      const absolutePath = path.resolve(rcPath)
      const rcConfig = await import(absolutePath)
      if (rcConfig.default?.defaults) {
        baseConfig = rcConfig.default.defaults
        if (debug) console.log('Loaded rc config:', rcConfig.default.defaults)
      }
    } catch (err) {
      console.error('Error loading .photo-flowrc.js:', err)
    }

    // Load local overrides from project root
    const localConfigPath = path.join(projectRoot, '.photo-flow.local.json')
    if (debug) console.log('Looking for local config at:', localConfigPath)
    let localConfig = {}
    if (await filesystem.existsAsync(localConfigPath)) {
      try {
        const contents = await filesystem.readAsync(localConfigPath, 'json')
        if (contents) {
          localConfig = contents
          if (debug) console.log('Loaded local config:', contents)
        }
      } catch (err) {
        console.error('Error loading local config:', err)
      }
    }

    // Deep merge function for nested objects
    const deepMerge = (target: any, source: any) => {
      if (!source) return target
      Object.keys(source).forEach((key) => {
        if (
          source[key] &&
          typeof source[key] === 'object' &&
          !Array.isArray(source[key])
        ) {
          if (!target[key]) Object.assign(target, { [key]: {} })
          deepMerge(target[key], source[key])
        } else {
          Object.assign(target, { [key]: source[key] })
        }
      })
      return target
    }

    // Create a fresh object for each merge to avoid mutation
    const mergedConfig = deepMerge(
      JSON.parse(JSON.stringify(baseConfig)), // Deep clone base config
      localConfig
    )

    if (debug) {
      console.log('Project root:', projectRoot)
      console.log('Base config:', baseConfig)
      console.log('Local config:', localConfig)
      console.log('Merged config:', mergedConfig)
    }

    return mergedConfig as Config
  } catch (err) {
    // If project root not found, return default config
    if (debug) {
      console.log('Using default config:', getDefaultConfig())
    }
    return getDefaultConfig()
  }
}

/**
 * Returns the default configuration
 */
function getDefaultConfig(): Config {
  return {
    paths: {
      camera: '~/Pictures/Camera',
      staging: '~/Pictures/Staging',
      archive: '~/Pictures/Archive',
      immich: '~/Pictures/Immich',
    },
    network: {
      smbServer: '',
      username: '',
      password: '',
      shares: [],
    },
  }
}

/**
 * Saves configuration to the local override file
 */
export async function saveConfig(
  toolbox: GluegunToolbox,
  config: Partial<Config>,
  scope: 'local' = 'local' // Only support local scope now
): Promise<void> {
  const { filesystem, parameters } = toolbox
  const debug = parameters?.options?.debug

  // Ensure we're in the project root
  const projectRoot = await ensureProjectRoot(toolbox)

  const configPath = path.join(projectRoot, '.photo-flow.local.json')
  if (debug) console.log(`Saving config to: ${configPath}`)

  // Load existing config
  let existingConfig = {}
  if (await filesystem.existsAsync(configPath)) {
    try {
      const contents = await filesystem.readAsync(configPath, 'json')
      if (contents) {
        existingConfig = contents
        if (debug) console.log('Loaded existing config:', contents)
      }
    } catch (err) {
      console.error('Error loading existing config:', err)
    }
  }

  // Deep merge the new config with existing
  const deepMerge = (target: any, source: any) => {
    if (!source) return target
    Object.keys(source).forEach((key) => {
      if (
        source[key] &&
        typeof source[key] === 'object' &&
        !Array.isArray(source[key])
      ) {
        if (!target[key]) Object.assign(target, { [key]: {} })
        deepMerge(target[key], source[key])
      } else {
        Object.assign(target, { [key]: source[key] })
      }
    })
    return target
  }

  const newConfig = deepMerge(existingConfig, config)
  if (debug) console.log('Saving new config:', newConfig)
  await filesystem.writeAsync(configPath, newConfig)
}
