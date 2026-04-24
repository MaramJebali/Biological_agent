"""Product data models - YOU BUILD"""

from dataclasses import dataclass, field
from typing import List, Optional

@dataclass
class ChemicalInProduct:
    """A single ingredient in a product"""
    name: str

@dataclass
class Product:
    """A product being analyzed"""
    product_id: str
    ingredient_list: List[ChemicalInProduct] = field(default_factory=list)
    product_usage: Optional[str] = None
    exposure_type: Optional[str] = None
    product_name: Optional[str] = None

@dataclass
class ProductsList:
    """Collection of products to analyze"""
    products_list: List[Product]
    
    def get_all_ingredient_names(self) -> List[str]:
        """Get unique ingredient names across all products"""
        names = set()
        for product in self.products_list:
            for ingredient in product.ingredient_list:
                names.add(ingredient.name)
        return list(names)
    
    def get_ingredient_frequencies(self) -> dict:
        """Count how many products contain each ingredient"""
        freq = {}
        for product in self.products_list:
            unique_in_product = set(ing.name for ing in product.ingredient_list)
            for name in unique_in_product:
                freq[name] = freq.get(name, 0) + 1
        return freq