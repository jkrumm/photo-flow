import { db, PhotoRecord } from './database'

export class PhotoRepository {
  async getPhoto(baseFilename: string): Promise<PhotoRecord | undefined> {
    await db.read()
    return db.data.photos[baseFilename]
  }

  async getAllPhotos(): Promise<PhotoRecord[]> {
    await db.read()
    return Object.values(db.data.photos)
  }

  async addPhoto(photo: PhotoRecord): Promise<void> {
    await db.read()
    db.data.photos[photo.baseFilename] = photo
    await db.write()
  }

  async updatePhoto(
    baseFilename: string,
    photo: Partial<PhotoRecord>
  ): Promise<void> {
    await db.read()
    const existing = db.data.photos[baseFilename]
    if (!existing) return

    db.data.photos[baseFilename] = {
      ...existing,
      ...photo,
    }
    await db.write()
  }

  async deletePhoto(baseFilename: string): Promise<void> {
    await db.read()
    delete db.data.photos[baseFilename]
    await db.write()
  }
}
