import React from 'react';
import type { ImageMetadata } from 'astro';

interface GalleryProps {
  images: {
    src: ImageMetadata;
    alt: string;
  }[];
}

export default function Gallery({ images }: GalleryProps) {
  return (
    <div className="gallery px-5 sm:px-gallery-mobile lg:px-gallery-desktop">
      {images.map((image, index) => (
        <div 
          key={index} 
          className="gallery-item my-5 sm:my-8 lg:my-10 overflow-hidden"
        >
          <img
            src={image.src.src}
            width={image.src.width}
            height={image.src.height}
            alt={image.alt}
            className="w-full h-auto object-contain"
            loading={index === 0 ? "eager" : "lazy"}
          />
        </div>
      ))}
    </div>
  );
}
