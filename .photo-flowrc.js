// Default configuration for photo-flow
module.exports = {
  defaults: {
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
  },
}
