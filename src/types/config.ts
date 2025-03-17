export interface SmbShare {
  name: string // e.g., 'HDD', 'SSD'
  sharePath: string // e.g., 'HDD', 'SSD' (share name on server)
  mountPoint: string // e.g., '/Volumes/HDD', '/Volumes/SSD'
}

export interface Config {
  paths: {
    camera: string
    staging: string
    archive: string
    immich: string
  }
  network: {
    smbServer: string // e.g., 'samba.jkrumm.dev'
    username?: string // Optional: same credentials for all shares
    password?: string // Optional: same credentials for all shares
    shares: SmbShare[] // Multiple shares on the same server
  }
}
