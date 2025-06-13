import fs from 'fs/promises';
import path from 'path';

export interface ImageData {
  src: any; // Astro ImageMetadata object
  alt: string;
  metadata: {
    filename: string;
    dimensions: string;
    camera_make: string;
    camera_model: string;
    iso: number;
    aperture: string;
    shutter_speed: string;
    focal_length: string;
    date_taken: string;
    rating: number;
    title?: string;
    description?: string;
    latitude?: number;
    longitude?: number;
  };
}

export async function getGalleryImages(): Promise<ImageData[]> {
  try {
    // Read metadata
    const metadataPath = path.resolve('src/metadata.json');
    const metadataContent = await fs.readFile(metadataPath, 'utf-8');
    const metadata = JSON.parse(metadataContent);

    // Simplified dynamic import with better error handling
    const imageModules = import.meta.glob<{ default: any }>('/src/images/*.{jpeg,jpg,png,gif,webp,avif,JPG,JPEG,PNG,GIF,WEBP,AVIF}', {
      eager: true 
    });
    
    // Create image map more efficiently
    const imageMap = Object.fromEntries(
      Object.entries(imageModules).map(([path, module]) => [
        path.split('/').pop()!,
        module.default
      ])
    );

    // Process images with better error handling
    const processedImages = await Promise.allSettled(
      metadata.images.map(async (imageData: any) => {
        const importedImage = imageMap[imageData.filename];
        
        if (!importedImage) {
          throw new Error(`Image not found: ${imageData.filename}`);
        }

        const alt = imageData.title || 
                   imageData.description || 
                   path.basename(imageData.filename, path.extname(imageData.filename));

        return {
          src: importedImage,
          alt,
          metadata: {
            filename: imageData.filename,
            dimensions: imageData.dimensions,
            camera_make: imageData.camera_make,
            camera_model: imageData.camera_model,
            iso: imageData.iso,
            aperture: imageData.aperture,
            shutter_speed: imageData.shutter_speed,
            focal_length: imageData.focal_length,
            date_taken: imageData.date_taken,
            rating: imageData.rating,
            title: imageData.title,
            description: imageData.description,
            latitude: imageData.latitude,
            longitude: imageData.longitude,
          },
          dateTaken: new Date(imageData.date_taken).getTime(), // Convert to timestamp for easier sorting
        };
      })
    );

    // Filter successful results and log failures
    const validImages = processedImages
      .filter((result): result is PromiseFulfilledResult<any> => {
        if (result.status === 'rejected') {
          console.warn('Failed to process image:', result.reason);
          return false;
        }
        return true;
      })
      .map(result => result.value);

    // Sort by date (newest first) and clean up
    return validImages
      .sort((a, b) => b.dateTaken - a.dateTaken)
      .map(({ dateTaken, ...rest }) => rest);

  } catch (error) {
    console.error('Error loading gallery images:', error);
    return [];
  }
}

// Simplified utility functions
export function getResponsiveImageWidth(index: number): number {
  return index < 3 ? 1600 : 1200;
}

export function optimizeImage(src: any, options: { width?: number; quality?: number } = {}) {
  // Astro handles optimization automatically
  return src;
}