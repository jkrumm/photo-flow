import { GluegunToolbox } from 'gluegun'
import { Config, SmbShare } from '../types/config'
import { loadConfig, saveConfig } from '../services/configLoader'
import { BaseCommand } from '../lib/base-command'

class ConfigCommand extends BaseCommand {
  name = 'config'
  description = 'View or update configuration settings'
  hidden = false

  async runFromProjectRoot(toolbox: GluegunToolbox): Promise<void> {
    const {
      print: { info, success, error, table },
      parameters: { first, second, options },
      prompt,
    } = toolbox

    // Load merged config
    const config = await loadConfig(toolbox)

    // Debug log the loaded config
    if (options.debug) {
      info('Loaded config:')
      info(JSON.stringify(config, null, 2))
    }

    // Subcommands
    const action = first

    switch (action) {
      case 'get': {
        // Get a specific config value
        const key = second
        if (!key) {
          error('Please specify a config key (e.g., paths.camera)')
          return
        }

        // Get nested value using path
        const getPath = (obj: any, path: string): any => {
          return path.split('.').reduce((acc, part) => {
            if (acc === undefined) return undefined
            return acc[part]
          }, obj)
        }

        const value = getPath(config, key)
        if (value === undefined) {
          error(`Config key "${key}" not found`)
          return
        }

        info(`${key}: ${JSON.stringify(value, null, 2)}`)
        break
      }

      case 'set': {
        // Set a specific config value
        const key = second
        if (!key) {
          error('Please specify a config key (e.g., paths.camera)')
          return
        }

        // Get current value for the prompt
        const getPath = (obj: any, path: string): any => {
          return path.split('.').reduce((acc, part) => {
            if (acc === undefined) return undefined
            return acc[part]
          }, obj)
        }
        const currentValue = getPath(config, key)

        // Handle different value types
        let value
        if (typeof currentValue === 'boolean') {
          const result = await prompt.confirm('Enter value?', currentValue)
          value = result
        } else if (Array.isArray(currentValue)) {
          info('Current value:')
          info(JSON.stringify(currentValue, null, 2))
          const result = await prompt.ask({
            type: 'input',
            name: 'value',
            message: 'Enter JSON array:',
            initial: JSON.stringify(currentValue),
          })
          try {
            value = JSON.parse(result.value)
          } catch (err) {
            error('Invalid JSON array')
            return
          }
        } else {
          const result = await prompt.ask({
            type: 'input',
            name: 'value',
            message: `Enter value for ${key}:`,
            initial:
              currentValue !== undefined ? String(currentValue) : undefined,
          })
          value = result.value
        }

        // Create new config object with updated value
        const setPath = (obj: any, path: string, value: any) => {
          const parts = path.split('.')
          const lastPart = parts.pop()!
          const target = parts.reduce((acc, part) => {
            if (!(part in acc)) acc[part] = {}
            return acc[part]
          }, obj)
          target[lastPart] = value
          return obj
        }

        const newConfig = setPath({}, key, value)

        // Save to local config file
        await saveConfig(toolbox, newConfig, 'local')

        success(`Updated ${key} = ${JSON.stringify(value, null, 2)}`)
        break
      }

      case 'list': {
        // List all configuration
        const rows: [string, string][] = []

        // Flatten config object into rows
        const flatten = (obj: any, prefix = '') => {
          if (!obj) return

          Object.entries(obj).forEach(([key, value]) => {
            const path = prefix ? `${prefix}.${key}` : key
            if (value && typeof value === 'object') {
              if (Array.isArray(value)) {
                rows.push([
                  path,
                  `[${value.length} items] ${JSON.stringify(value)}`,
                ])
              } else {
                flatten(value, path)
              }
            } else {
              if (key === 'password' && value) {
                rows.push([path, '********'])
              } else {
                rows.push([path, String(value)])
              }
            }
          })
        }

        flatten(config)

        if (rows.length === 0) {
          info('No configuration values found')
          if (options.debug) {
            info('Raw config object:')
            info(JSON.stringify(config, null, 2))
          }
          return
        }

        // Display as table
        table([['Key', 'Value'], ...rows])
        break
      }

      case 'init': {
        // Initialize config with defaults
        const defaultConfig: Config = {
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

        // Ask for each path
        const paths = (await prompt.ask([
          {
            type: 'input',
            name: 'camera',
            message: 'Camera directory path:',
            initial: defaultConfig.paths.camera,
          },
          {
            type: 'input',
            name: 'staging',
            message: 'Staging directory path:',
            initial: defaultConfig.paths.staging,
          },
          {
            type: 'input',
            name: 'archive',
            message: 'Archive directory path:',
            initial: defaultConfig.paths.archive,
          },
          {
            type: 'input',
            name: 'immich',
            message: 'Immich directory path:',
            initial: defaultConfig.paths.immich,
          },
        ])) as Config['paths']

        // Ask for network settings if needed
        const useNetwork = await prompt.confirm('Configure network settings?')
        let network = defaultConfig.network

        if (useNetwork) {
          const baseNetwork = await prompt.ask([
            {
              type: 'input',
              name: 'smbServer',
              message: 'SMB server address:',
            },
            {
              type: 'input',
              name: 'username',
              message: 'SMB username (optional):',
            },
            {
              type: 'password',
              name: 'password',
              message: 'SMB password (optional):',
            },
          ])

          const shares: SmbShare[] = []
          let addMore = true

          while (addMore) {
            const share = (await prompt.ask([
              {
                type: 'input',
                name: 'name',
                message: 'Share name (e.g., HDD, SSD):',
              },
              {
                type: 'input',
                name: 'sharePath',
                message: 'Share path on server:',
              },
              {
                type: 'input',
                name: 'mountPoint',
                message: 'Local mount point:',
                initial: (answers: any) => `/Volumes/${answers.name}`,
              },
            ])) as SmbShare

            shares.push(share)
            addMore = await prompt.confirm('Add another share?')
          }

          network = {
            ...baseNetwork,
            shares,
          } as Config['network']
        }

        // Save configuration
        const newConfig: Config = {
          paths,
          network,
        }

        // Save to local config file
        await saveConfig(toolbox, newConfig, 'local')

        success('Configuration initialized successfully!')
        break
      }

      default: {
        info(`
Photo Flow Configuration

Commands:
  config list                Show all settings
  config get <key>          Get a specific setting
  config set <key>          Set a specific setting
  config init               Initialize configuration interactively

Options:
  --debug                   Show debug information

Examples:
  photo-flow config get paths.camera
  photo-flow config set paths.staging ~/Pictures/Staging
  photo-flow config list
  photo-flow config list --debug
        `)
        break
      }
    }
  }
}

module.exports = new ConfigCommand().createCommand()
