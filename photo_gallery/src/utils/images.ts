import { getImage } from 'astro:assets';
import type { ImageMetadata } from 'astro';
import fs from 'fs/promises';
import path from 'path';

// Define interface for image metadata
export interface ImageData {
  src: ImageMetadata;
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
    rating?: number;
    title?: string;
    description?: string;
    latitude?: number;
    longitude?: number;
  };
}

// Function to get all images from the metadata.json file
export async function getGalleryImages(): Promise<ImageData[]> {
  try {
    // Path to the metadata.json file
    const metadataPath = path.resolve('public/metadata.json');

    // Read and parse the metadata.json file
    const metadataContent = await fs.readFile(metadataPath, 'utf-8');
    const metadata = JSON.parse(metadataContent);

    // Create image metadata for each file in the metadata
    const images = metadata.images.map((imageData: any) => {
      // Extract dimensions
      const [width, height] = imageData.dimensions.split('x').map(Number);

      // Create a metadata object
      const imageMetadata: ImageMetadata = {
        src: `/images/${imageData.filename}`,
        width: width,
        height: height,
        format: path.extname(imageData.filename).substring(1).toLowerCase() as any,
      };

      // Use filename as alt text, or title if available
      const alt = imageData.title || path.basename(imageData.filename, path.extname(imageData.filename));

      return {
        src: imageMetadata,
        alt: alt,
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
        dateTaken: imageData.date_taken, // Preserve date_taken for sorting
      };
    });

    // Sort images by date_taken in descending order (newest first)
    const sortedImages = images.sort((a, b) => {
      const dateA = new Date(a.dateTaken);
      const dateB = new Date(b.dateTaken);
      return dateB.getTime() - dateA.getTime(); // Descending order
    });

    // Remove the dateTaken property before returning
    return sortedImages.map(({ dateTaken, ...rest }) => rest);
  } catch (error) {
    console.error('Error loading gallery images from metadata:', error);
    return [];
  }
}

// Function to optimize an image for display
export async function optimizeImage(
  src: string,
  options: { width: number; height?: number; format?: 'avif' | 'jpeg' | 'png' | 'webp' }
) {
  try {
    // In a real implementation, we would use getImage from astro:assets
    // This is a placeholder for demonstration
    return {
      src,
      ...options,
    };
  } catch (error) {
    console.error('Error optimizing image:', error);
    return { src, width: options.width, height: options.height || 0 };
  }
}
