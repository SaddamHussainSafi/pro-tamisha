import { useState } from "react";
import { Icon } from "./Icon";

/**
 * A small square product photo for table rows (2.25rem: 40 px at the app's text size; shown
 * whole: object-fit contain).
 * Photos are the static files public/products/<SKU>.webp; a product without one gets a
 * neutral placeholder. Decorative: the product's name is always next to it.
 */
export function ProductThumb({ sku, className = "" }: { sku: string; className?: string }) {
  const [missingFor, setMissingFor] = useState<string | null>(null);
  return (
    <span aria-hidden className={`grid size-9 shrink-0 place-items-center overflow-hidden rounded-md bg-subtle text-ink-muted/60 ${className}`}>
      {missingFor === sku ? (
        <Icon name="products" size={16} />
      ) : (
        <img src={`/products/${sku.toUpperCase()}.webp`} alt="" width={40} height={40} loading="lazy" decoding="async" draggable={false} onError={() => setMissingFor(sku)} className="size-full object-contain" />
      )}
    </span>
  );
}
