import { Config } from './config'

export interface DirectoryStatus {
  path: string
  exists: boolean
  type: 'camera' | 'staging' | 'archive' | 'immich'
}

export interface PhotoFlowState {
  directories: DirectoryStatus[]
  smbConnected: boolean
  config: Config | null
}
