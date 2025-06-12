import React, { useState } from 'react';
import type { ImageData } from '../utils/images';
import ImageModal from './ImageModal';

interface GalleryProps {
  images: ImageData[];
}

export default function Gallery({ images }: GalleryProps) {
  const [selectedImage, setSelectedImage] = useState<ImageData | null>(null);

  const handleImageClick = (image: ImageData) => {
    setSelectedImage(image);
    // Prevent scrolling when modal is open
    document.body.style.overflow = 'hidden';
  };

  const handleCloseModal = () => {
    setSelectedImage(null);
    // Restore scrolling when modal is closed
    document.body.style.overflow = '';
  };

  return (
    <>
      <div className="gallery px-5 sm:px-gallery-mobile lg:px-gallery-desktop">
        {images.map((image, index) => (
          <div 
            key={index} 
            className="gallery-item my-5 sm:my-8 lg:my-10 overflow-hidden cursor-pointer"
            onClick={() => handleImageClick(image)}
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

      {/* Image Modal */}
      <ImageModal 
        image={selectedImage} 
        onClose={handleCloseModal} 
      />
    </>
  );
}
