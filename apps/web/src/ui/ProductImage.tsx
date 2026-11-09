import { useState, type ReactNode } from "react";

/** Product photos are static files named by SKU: public/products/<SKU>.webp (see CREDITS.md there). */
const productImageUrl = (sku: string) => `/products/${sku.toUpperCase()}.webp`;

/**
 * The product's photo, filling its box. Decorative (the product name is always next to it), so
 * the alt text is empty. Shows `fallback` when the product has no photo yet.
 */
export function ProductImage({ sku, fallback = null, className = "" }: { sku: string; fallback?: ReactNode; className?: string }) {
  const [missing, setMissing] = useState(false);
  if (missing) return <>{fallback}</>;
  return (
    <img
      src={productImageUrl(sku)}
      alt=""
      loading="lazy"
      decoding="async"
      draggable={false}
      onError={() => setMissing(true)}
      className={`size-full object-cover ${className}`}
    />
  );
}
