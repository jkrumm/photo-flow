import React, { useState, useEffect } from 'react';
import type { ImageData } from '../utils/images';
import LocationMap from './LocationMap';

interface ImageModalProps {
  image: ImageData | null;
  onClose: () => void;
}

export default function ImageModal({ image, onClose }: ImageModalProps) {
  const [isOpen, setIsOpen] = useState(false);

  // Handle animation on open/close
  useEffect(() => {
    if (image) {
      // Small delay to allow CSS transition to work
      setTimeout(() => setIsOpen(true), 50);
    } else {
      setIsOpen(false);
    }
  }, [image]);

  // Handle ESC key to close modal
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        handleClose();
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, []);

  // Handle close with animation
  const handleClose = () => {
    setIsOpen(false);
    // Wait for animation to complete before calling onClose
    setTimeout(onClose, 300);
  };

  if (!image) return null;

  // Format date for display
  const formatDate = (dateString: string) => {
    const date = new Date(dateString);
    return date.toLocaleDateString(undefined, {
      year: 'numeric',
      month: 'long',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit'
    });
  };

  return (
    <div 
      className={`fixed inset-0 z-50 flex flex-col items-center justify-center overflow-y-auto bg-black bg-opacity-90 transition-opacity duration-300 ${
        isOpen ? 'opacity-100 animate-[fadeIn_0.3s_ease-out]' : 'opacity-0'
      }`}
      onClick={handleClose}
    >
      {/* Card container */}
      <div 
        className={`inline-block mx-auto my-8 transition-transform duration-300 ${
          isOpen ? 'scale-100 animate-[scaleIn_0.3s_ease-out]' : 'scale-95'
        }`}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Card layout with image on top and info below */}
        <div className="bg-white overflow-hidden">
          {/* Image at the top (without border) */}
          <div className="w-full">
            <img
              src={image.src.src}
              width={image.src.width}
              height={image.src.height}
              alt={image.alt}
              className="w-full object-contain max-h-[70vh]"
              style={{ maxWidth: '100%' }}
            />
          </div>

          {/* Metadata section below the image */}
          <div className="p-6 relative">
            {/* Display map as background if location data is available */}
            {image.metadata.latitude && image.metadata.longitude && (
              <div className="absolute inset-0 z-0 overflow-hidden" style={{ maxHeight: "120px" }}>
                <LocationMap 
                  latitude={image.metadata.latitude} 
                  longitude={image.metadata.longitude} 
                />
              </div>
            )}

            <div className="flex flex-col relative z-10">
              <div className="mb-4">
                <p className="mb-2">{image.metadata.camera_make} {image.metadata.camera_model} | ISO {image.metadata.iso} | {image.metadata.aperture} | {image.metadata.shutter_speed} | {image.metadata.focal_length}</p>
                <p><span className="font-medium">Date Taken:</span> {formatDate(image.metadata.date_taken)}</p>
              </div>

              {image.metadata.description && (
                <div className="mt-2">
                  <p className="mt-1">{image.metadata.description}</p>
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
