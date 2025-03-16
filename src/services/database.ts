import { join } from 'path'
import { Low } from 'lowdb'
import { JSONFile } from 'lowdb/node'

// Types for our database schema
export interface PhotoRecord {
  baseFilename: string // Primary key, filename without extension
  extensions: string[] // All file extensions for this base filename
  locations: {
    camera: boolean
    staging: boolean
    immich: boolean
    archive: boolean
  }
}

// Database structure
export interface DbSchema {
  photos: Record<string, PhotoRecord>
}

// Default data
const defaultData: DbSchema = {
  photos: {},
}

// Initialize database
const dbPath = join(process.cwd(), 'data', 'db.json')
const adapter = new JSONFile<DbSchema>(dbPath)
export const db = new Low(adapter, defaultData)

// Initialize database if it doesn't exist
export async function initDatabase(): Promise<void> {
  await db.read()
  await db.write()
}
