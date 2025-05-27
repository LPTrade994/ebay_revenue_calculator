import streamlit as st
import json
from decimal import Decimal, ROUND_HALF_UP

# --- Utility Functions ---
def to_decimal(value, precision='0.01'):
    """Converts a float or string to Decimal with specified precision."""
    return Decimal(str(value)).quantize(Decimal(precision), rounding=ROUND_HALF_UP)

def to_percentage_decimal(value):
    """Converts a percentage float (e.g., 0.05) to Decimal for calculations."""
    return Decimal(str(value))

# --- Load Fee Data ---
@st.cache_data
def load_fee_data(file_path="ebay_professional_fees_it.json"):
    with open(file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    # Pre-process category_ids for faster lookup
    category_to_fvf_group = {}
    for group in data['final_value_fees']:
        for cat_id in group['category_ids']:
            category_to_fvf_group[cat_id] = group
    data['_category_map'] = category_to_fvf_group

    vehicle_cats = {}
    for key, vehicle_data in data['vehicles'].items():
        if 'category_ids' in vehicle_data:
            for cat_id in vehicle_data['category_ids']:
                vehicle_cats[cat_id] = {
                    "type": key,
                    "insertion_fee": to_decimal(vehicle_data['insertion_fee']),
                    "final_value_fee": to_decimal(vehicle_data['final_value_fee']) # This is a fixed amount
                }
    data['_vehicle_category_map'] = vehicle_cats
    return data

FEE_DATA = load_fee_data()

# --- Calculation Functions ---

def get_final_value_fee_rate_and_group(category_id, total_sale_price):
    """
    Determines the FVF rate and group based on category ID and total sale price.
    Returns a tuple: (variable_fvf_amount, fvf_group_name, is_tiered)
    or (fixed_fvf_amount, "Vehicle", False) if it's a vehicle with fixed FVF.
    """
    total_sale_price_dec = to_decimal(total_sale_price)

    # Check for special vehicle categories first
    if category_id in FEE_DATA['_vehicle_category_map']:
        vehicle_info = FEE_DATA['_vehicle_category_map'][category_id]
        if vehicle_info['type'] in ["high_value_vehicles", "motorcycles_and_others"]:
            # These vehicles have a fixed final value fee, not a rate
            return vehicle_info['final_value_fee'], f"Veicoli ({vehicle_info['type']})", False # Return fixed fee

    fvf_group_data = FEE_DATA['_category_map'].get(category_id)
    if not fvf_group_data:
        # Fallback to "Other_categories_including_clothing_beauty" if category ID not found in specific groups
        # This is an assumption, you might want a more robust error or a default
        st.warning(f"ID Categoria {category_id} non trovato specificamente, si applica la tariffa 'Altre categorie'.")
        for group in FEE_DATA['final_value_fees']:
            if group['group'] == "Other_categories_including_clothing_beauty":
                fvf_group_data = group
                break
        if not fvf_group_data: # Should not happen if JSON is correct
             return Decimal('0'), "Categoria non trovata", False


    group_name = fvf_group_data['group']

    if 'variable_rate' in fvf_group_data:
        rate = to_percentage_decimal(fvf_group_data['variable_rate'])
        return total_sale_price_dec * rate, group_name, False
    elif 'tiers' in fvf_group_data:
        calculated_fvf = Decimal('0')
        remaining_price = total_sale_price_dec
        
        tiers = sorted(fvf_group_data['tiers'], key=lambda x: x.get('up_to_eur', x.get('from_eur', float('inf'))))

        for i, tier in enumerate(tiers):
            tier_rate = to_percentage_decimal(tier['rate'])
            
            if 'up_to_eur' in tier:
                tier_limit = to_decimal(tier['up_to_eur'])
                applicable_amount = min(remaining_price, tier_limit if i == 0 else tier_limit - to_decimal(tiers[i-1].get('up_to_eur', tiers[i-1].get('to_eur', 0))))
                
                if total_sale_price_dec <= tier_limit: # Current tier handles the rest or all
                    calculated_fvf += remaining_price * tier_rate
                    remaining_price = Decimal('0')
                    break
                else: # Price exceeds this tier
                    # amount_in_this_tier needs to be the portion of the price that falls into this tier's range
                    # For the first tier: min(total_price, up_to_eur)
                    # For subsequent up_to_eur tiers: min(remaining_price, up_to_eur_current - up_to_eur_previous)
                    prev_tier_upper_bound = to_decimal(tiers[i-1]['up_to_eur']) if i > 0 and 'up_to_eur' in tiers[i-1] else Decimal('0')
                    
                    # If the previous tier was 'from_eur'/'to_eur', this logic for prev_tier_upper_bound needs adjustment.
                    # Assuming tiers are structured like: up_to_X, then from_X_to_Y, then above_Y
                    # Or: up_to_X, up_to_Y (where Y>X), up_to_Z (Z>Y)
                    
                    # Simplified logic for typical eBay tier structures:
                    if i == 0: # First tier
                        amount_for_this_tier_calc = tier_limit
                    else: # Subsequent 'up_to_eur' tiers
                        amount_for_this_tier_calc = tier_limit - to_decimal(tiers[i-1].get('up_to_eur', tiers[i-1].get('to_eur',0)))
                    
                    price_in_tier = min(remaining_price, amount_for_this_tier_calc)
                    calculated_fvf += price_in_tier * tier_rate
                    remaining_price -= price_in_tier
                    if remaining_price <= Decimal('0'): break

            elif 'from_eur' in tier and 'to_eur' in tier:
                tier_from = to_decimal(tier['from_eur'])
                tier_to = to_decimal(tier['to_eur'])
                # This tier applies to the portion of the price between tier_from and tier_to
                if total_sale_price_dec > tier_from:
                    amount_in_tier_range = tier_to - tier_from
                    # Price portion that falls into this tier's range, considering what's already processed
                    # This part is tricky and depends on how tiers are defined.
                    # Assuming from_eur is exclusive of previous tier's limit.
                    # Example: up to 100, then 100.01 to 990
                    # If total is 500:
                    # Tier 1 (up to 100): processes 100
                    # Tier 2 (100.01 to 990): remaining is 400. Processes 400.

                    # Let's assume 'remaining_price' correctly reflects the portion above previous tier limits
                    price_for_this_tier = min(remaining_price, amount_in_tier_range)
                    if total_sale_price_dec > tier_from: # only apply if price actually enters this tier
                         # The amount to consider for this tier is min(remaining_price, tier_to - tier_from)
                         # This should be: what portion of the *original total_sale_price* falls within this tier's absolute bounds,
                         # that hasn't been covered by previous tiers.
                         # A simpler way for tiered calculation:
                         # 1. Amount in first tier: min(total_sale_price, tier1_limit) * rate1
                         # 2. Amount in second tier: min(max(0, total_sale_price - tier1_limit), tier2_span) * rate2
                         # ... this is what the loop is trying to do with remaining_price.

                        # If current tier is 'from X to Y':
                        # Amount already processed by previous tiers is (total_sale_price_dec - remaining_price)
                        # This tier starts at tier_from.
                        
                        # If total_sale_price > tier_from:
                        amount_to_tax_in_this_tier = min(total_sale_price_dec, tier_to) - tier_from
                        amount_to_tax_in_this_tier = max(Decimal('0'), amount_to_tax_in_this_tier) # ensure non-negative
                        
                        #This logic needs to be more robust for mixed tier types
                        # Let's use a standard tiered calculation based on total_sale_price
                        # This means we re-calculate for each tier block based on the original price
                        # For this specific JSON, tiers are usually: up_to_X, above_X OR up_to_X, from_X_to_Y, above_Y

                        # The current 'remaining_price' logic should work if tiers are sequential and cover ranges.
                        
                        amount_processed_in_tier = min(remaining_price, tier_to - tier_from)
                        calculated_fvf += amount_processed_in_tier * tier_rate
                        remaining_price -= amount_processed_in_tier
                        if remaining_price <= Decimal('0'): break
            
            elif 'above_eur' in tier:
                # This is the last tier, applies to any remaining amount
                if remaining_price > Decimal('0'):
                    calculated_fvf += remaining_price * tier_rate
                    remaining_price = Decimal('0')
                break # Should be the last tier

        return calculated_fvf, group_name, True
    
    return Decimal('0'), "Sconosciuto", False # Should not happen

def calculate_fees(item_price, shipping_cost, item_cost, category_id, buyer_country,
                   seller_status, high_inad_surcharge, store_subscription,
                   num_listings_this_month, listing_type, # for insertion fees
                   add_subtitle, reserve_price_value, use_reserve_price,
                   apply_vat, vat_rate_input):
    """
    Calculates all eBay fees and profit.
    Returns a dictionary with detailed fee breakdown and profit.
    """
    results = {}
    total_fees_pre_vat = Decimal('0')

    item_price_dec = to_decimal(item_price)
    shipping_cost_dec = to_decimal(shipping_cost)
    item_cost_dec = to_decimal(item_cost)
    total_sale_price_dec = item_price_dec + shipping_cost_dec
    results['total_sale_price'] = total_sale_price_dec

    # 1. Final Value Fee (FVF)
    is_vehicle_fixed_fvf = False
    if category_id in FEE_DATA['_vehicle_category_map']:
        vehicle_info = FEE_DATA['_vehicle_category_map'][category_id]
        if vehicle_info['type'] in ["high_value_vehicles", "motorcycles_and_others"]:
            base_fvf_amount = vehicle_info['final_value_fee'] # This is a fixed amount
            fvf_group_name = f"Veicoli ({vehicle_info['type']})"
            results['fvf_calculation_details'] = f"Tariffa fissa per {fvf_group_name}"
            is_vehicle_fixed_fvf = True
        else: # Should not happen with current JSON structure for these types
            base_fvf_amount, fvf_group_name, is_tiered = get_final_value_fee_rate_and_group(category_id, total_sale_price_dec)
            results['fvf_calculation_details'] = f"Tariffa {'a scaglioni' if is_tiered else 'variabile'} per '{fvf_group_name}'"
    else:
        base_fvf_amount, fvf_group_name, is_tiered = get_final_value_fee_rate_and_group(category_id, total_sale_price_dec)
        results['fvf_calculation_details'] = f"Tariffa {'a scaglioni' if is_tiered else 'variabile'} per '{fvf_group_name}'"

    base_fvf_amount = to_decimal(base_fvf_amount) # Ensure it's Decimal
    results['base_fvf_amount_raw'] = base_fvf_amount
    results['fvf_group_name'] = fvf_group_name

    # Apply discounts/surcharges to FVF (only if not a fixed vehicle FVF)
    effective_fvf = base_fvf_amount
    results['fvf_discounts_surcharges'] = []

    if not is_vehicle_fixed_fvf: # Discounts/surcharges typically don't apply to fixed vehicle FVFs
        if seller_status == "Venditore Affidabilità Top":
            discount_rate = to_percentage_decimal(FEE_DATA['discounts_surcharges']['top_rated_seller_discount_rate'])
            discount_amount = base_fvf_amount * abs(discount_rate) # abs because rate is negative
            effective_fvf -= discount_amount
            results['fvf_discounts_surcharges'].append({
                "name": "Sconto Venditore Affidabilità Top",
                "rate_on_fvf": abs(discount_rate) * 100, # display as positive percentage
                "amount": -discount_amount # negative for display
            })
        
        if high_inad_surcharge:
            surcharge_rate = to_percentage_decimal(FEE_DATA['discounts_surcharges']['high_INAD_surcharge_rate'])
            surcharge_amount = base_fvf_amount * surcharge_rate
            effective_fvf += surcharge_amount
            results['fvf_discounts_surcharges'].append({
                "name": "Sovraccarico per controversie 'Non conforme'",
                "rate_on_fvf": surcharge_rate * 100,
                "amount": surcharge_amount
            })

        if seller_status == "Sotto lo standard":
            surcharge_rate = to_percentage_decimal(FEE_DATA['discounts_surcharges']['below_standard_surcharge_rate'])
            surcharge_amount = base_fvf_amount * surcharge_rate
            effective_fvf += surcharge_amount
            results['fvf_discounts_surcharges'].append({
                "name": "Sovraccarico Venditore Sotto lo Standard",
                "rate_on_fvf": surcharge_rate * 100,
                "amount": surcharge_amount
            })
    
    results['final_value_fee'] = to_decimal(effective_fvf)
    total_fees_pre_vat += results['final_value_fee']

    # 2. Regulatory Operating Fee
    reg_fee_rate = to_percentage_decimal(FEE_DATA['constants']['regulatory_compliance_fee_rate'])
    regulatory_fee = to_decimal(total_sale_price_dec * reg_fee_rate)
    results['regulatory_fee'] = regulatory_fee
    total_fees_pre_vat += regulatory_fee

    # 3. International Fee
    international_fee_rate_key = ""
    if buyer_country in ["Austria", "Belgio", "Cipro", "Estonia", "Finlandia", "Francia", "Germania", "Grecia", "Irlanda", "Italia", "Lettonia", "Lituania", "Lussemburgo", "Malta", "Paesi Bassi", "Portogallo", "Slovacchia", "Slovenia", "Spagna", "Svezia"]:
        international_fee_rate_key = "Eurozone_Sweden" # Svezia is listed with Eurozona
    elif buyer_country == "Regno Unito":
        international_fee_rate_key = "United_Kingdom"
    elif buyer_country in ["Stati Uniti", "Canada"]:
        international_fee_rate_key = "United_States_Canada"
    elif buyer_country in ["Albania", "Andorra", "Bielorussia", "Bosnia ed Erzegovina", "Bulgaria", "Croazia", "Danimarca", "Fær Øer", "Gibilterra", "Guernsey", "Islanda", "Isola di Man", "Jersey", "Liechtenstein", "Macedonia del Nord", "Moldavia", "Monaco", "Montenegro", "Norvegia", "Polonia", "Repubblica Ceca", "Romania", "Russia", "San Marino", "Serbia", "Svalbard e Jan Mayen", "Svizzera", "Ucraina", "Ungheria", "Vaticano"]:
        # This group might map to "Europe_non_eurozone_Sweden_UK"
        international_fee_rate_key = "Europe_non_eurozone_Sweden_UK"
    else: # Default to Rest_of_world for unlisted countries
        international_fee_rate_key = "Rest_of_world"
    
    international_fee_rate = to_percentage_decimal(FEE_DATA['international_fee_rates'][international_fee_rate_key])
    international_fee = to_decimal(total_sale_price_dec * international_fee_rate)
    results['international_fee'] = international_fee
    results['international_fee_details'] = f"Paese: {buyer_country}, Tariffa: {international_fee_rate*100:.1f}% ({international_fee_rate_key})"
    total_fees_pre_vat += international_fee

    # 4. Fixed Order Fee
    fixed_order_fee = to_decimal(FEE_DATA['constants']['fixed_order_fee_eur'])
    results['fixed_order_fee'] = fixed_order_fee
    total_fees_pre_vat += fixed_order_fee

    # 5. Insertion Fees (simplified: assumes one new listing)
    # For a more accurate calculation, this needs to track total listings per month vs free allowances.
    # This part calculates the fee for THIS listing.
    insertion_fee = Decimal('0')
    insertion_fee_details = "Nessuna tariffa di inserzione (presupponendo quota gratuita non superata o negozio con inserzioni illimitate)"

    is_vehicle_insertion = False
    if category_id in FEE_DATA['_vehicle_category_map']:
        vehicle_info_insert = FEE_DATA['_vehicle_category_map'][category_id]
        if vehicle_info_insert['type'] in ["high_value_vehicles", "motorcycles_and_others"]:
            insertion_fee = vehicle_info_insert['insertion_fee'] # This is a fixed amount
            insertion_fee_details = f"Tariffa di inserzione fissa per veicoli ({vehicle_info_insert['type']})"
            is_vehicle_insertion = True
    
    if not is_vehicle_insertion:
        if store_subscription == "Nessuno":
            fee_key = "auction" if listing_type == "Asta" else "buy_it_now"
            insertion_fee = to_decimal(FEE_DATA['insertion_fees']['non_store'][fee_key])
            insertion_fee_details = f"Tariffa inserzione '{listing_type}' senza negozio"
        else:
            store_data = FEE_DATA['insertion_fees']['store_subscriptions'][store_subscription]
            free_listings_key = f"free_{listing_type.lower().replace(' ', '_')}_listings"
            extra_listing_fee_key = f"extra_listing_fee_{listing_type.lower().replace(' ', '_')}"

            free_allowance = store_data[free_listings_key]
            if free_allowance == "unlimited":
                insertion_fee = Decimal('0')
                insertion_fee_details = f"Inserzioni '{listing_type}' illimitate con negozio {store_subscription}"
            elif num_listings_this_month > free_allowance:
                insertion_fee = to_decimal(store_data[extra_listing_fee_key])
                insertion_fee_details = f"Tariffa inserzione extra '{listing_type}' con negozio {store_subscription} (superata quota gratuita)"
            else:
                insertion_fee = Decimal('0') # Within free allowance
                insertion_fee_details = f"Inserzione '{listing_type}' gratuita con negozio {store_subscription} (rientra nella quota)"
    
    results['insertion_fee'] = insertion_fee
    results['insertion_fee_details'] = insertion_fee_details
    total_fees_pre_vat += insertion_fee
    
    # 6. Listing Upgrades
    results['listing_upgrades_fees'] = []
    listing_upgrade_total_fee = Decimal('0')

    if add_subtitle:
        subtitle_fee = to_decimal(FEE_DATA['listing_upgrades']['subtitle'])
        results['listing_upgrades_fees'].append({"name": "Sottotitolo", "fee": subtitle_fee})
        listing_upgrade_total_fee += subtitle_fee

    if use_reserve_price and reserve_price_value > 0:
        # Check if it's a vehicle with special reserve price fee
        is_vehicle_cat_for_reserve = False
        if category_id in FEE_DATA['_vehicle_category_map']:
            # Check if it's any vehicle category defined under "vehicles"
            # The JSON gives a specific "vehicle_reserve_price_fee" not tied to sub-types of vehicles.
            is_vehicle_cat_for_reserve = True # Assume any category in vehicle_map might use this

        if is_vehicle_cat_for_reserve and "vehicle_reserve_price_fee" in FEE_DATA['vehicles']:
             # This seems to be a general vehicle reserve price fee, overriding the percentage one
            reserve_fee = to_decimal(FEE_DATA['vehicles']['vehicle_reserve_price_fee'])
            reserve_fee_detail = f"Tariffa fissa prezzo di riserva per veicoli: {reserve_fee} €"
        else: # Standard reserve price calculation
            rp_config = FEE_DATA['listing_upgrades']['reserve_price']
            reserve_price_dec = to_decimal(reserve_price_value)
            reserve_fee = reserve_price_dec * to_percentage_decimal(rp_config['percentage_rate'])
            reserve_fee = max(to_decimal(rp_config['min_fee']), reserve_fee)
            reserve_fee = min(to_decimal(rp_config['max_fee']), reserve_fee)
            reserve_fee = to_decimal(reserve_fee)
            reserve_fee_detail = (f"Prezzo di riserva ({rp_config['percentage_rate']*100}% su {reserve_price_dec}€, "
                                  f"min {rp_config['min_fee']}€, max {rp_config['max_fee']}€)")

        results['listing_upgrades_fees'].append({"name": reserve_fee_detail, "fee": reserve_fee})
        listing_upgrade_total_fee += reserve_fee
        
    # "second_category_fee" is complex ("same as insertion fee for second category")
    # For now, we'll skip or simplify. If simplified, could be a checkbox "Add second category"
    # which just adds another `insertion_fee` amount.
    # Promoted listings are too variable (CPC or upfront).

    results['listing_upgrade_total_fee'] = listing_upgrade_total_fee
    total_fees_pre_vat += listing_upgrade_total_fee

    # --- Totals ---
    results['total_fees_pre_vat'] = to_decimal(total_fees_pre_vat)
    
    vat_amount = Decimal('0')
    if apply_vat:
        vat_rate_dec = to_percentage_decimal(vat_rate_input / 100)
        vat_amount = to_decimal(results['total_fees_pre_vat'] * vat_rate_dec)
    results['vat_amount'] = vat_amount
    results['total_fees_incl_vat'] = results['total_fees_pre_vat'] + vat_amount

    # Profit Calculation
    # Profit = (Item Price + Shipping) - Item Cost - Total Fees (pre-VAT, as VAT is on fees, not profit directly for seller)
    # Or, if seller pays VAT on fees and can't reclaim: Profit = (Item Price + Shipping) - Item Cost - Total Fees (incl. VAT)
    # Let's assume profit is calculated against fees *before* seller's VAT on those fees.
    # The example image subtracts fees *including* their VAT from the seller's perspective for final cost.
    # Let's show both.

    results['profit_if_vat_on_fees_is_cost'] = total_sale_price_dec - item_cost_dec - results['total_fees_incl_vat']
    results['profit_if_vat_on_fees_reclaimed'] = total_sale_price_dec - item_cost_dec - results['total_fees_pre_vat']
    
    return results

# --- Streamlit UI ---
st.set_page_config(page_title="Calcolatore Commissioni eBay", layout="wide")
st.title("📊 Calcolatore Commissioni eBay (Italia)")
st.caption(f"Basato sulle tariffe professionali del: {FEE_DATA['generated_on']}")

st.sidebar.header("Parametri della Vendita")

# Use columns for better layout
col1, col2 = st.sidebar.columns(2)

with col1:
    item_price_input = st.number_input("Prezzo dell'oggetto (€)", min_value=0.01, value=274.90, step=0.01, format="%.2f")
    shipping_cost_input = st.number_input("Costo di spedizione (€)", min_value=0.00, value=14.99, step=0.01, format="%.2f")
    item_cost_input = st.number_input("Costo di acquisto dell'oggetto (€)", min_value=0.00, value=150.00, step=0.01, format="%.2f", help="Il costo che hai sostenuto per l'oggetto.")
    category_id_input = st.number_input("ID Categoria eBay", min_value=1, value=171485, help="Es. 171485 per 'Console e Videogiochi'. Trova l'ID su eBay.") # Example: PlayStation Portal (Videogiochi e Console) -> Tech devices (6.5%) -> 171485

with col2:
    buyer_country_options = ["Malta"] + sorted(list(FEE_DATA['international_fee_rates'].keys())) # Add Malta for example
    # Create a more user-friendly list of countries
    country_display_names = {
        "Eurozone_Sweden": "Paesi Eurozona + Svezia",
        "Europe_non_eurozone_Sweden_UK": "Europa (non Eurozona, non UK) ",
        "United_Kingdom": "Regno Unito",
        "United_States_Canada": "Stati Uniti / Canada",
        "Rest_of_world": "Resto del mondo"
    }
    # For simplicity, directly mapping to relevant countries from example or common ones
    # A full country list mapped to these groups would be extensive.
    # Using example countries for now.
    buyer_country_input = st.selectbox("Paese acquirente",
                                       options=["Italia", "Malta", "Germania", "Francia", "Spagna", "Regno Unito", "Stati Uniti", "Altro (Resto del Mondo)"],
                                       index=1) # Default to Malta for the example
    
    seller_status_input = st.selectbox("Stato venditore",
                                       options=["Standard", "Venditore Affidabilità Top", "Sotto lo standard"],
                                       index=1) # Default to Top for example
    high_inad_input = st.checkbox("Alto tasso di 'Non conformi' (INAD)?", value=False)


st.sidebar.header("Opzioni di Inserzione e Negozio")
col3, col4 = st.sidebar.columns(2)

with col3:
    store_subscription_input = st.selectbox("Tipo di Negozio eBay",
                                            options=["Nessuno"] + list(FEE_DATA['insertion_fees']['store_subscriptions'].keys()),
                                            index=0)
    listing_type_input = st.radio("Tipo di Inserzione", ["Compralo Subito", "Asta"], index=0, horizontal=True)
    # Simplified num_listings_this_month - assumes this is the Nth listing *potentially* incurring a fee
    num_listings_input = st.number_input("Questa è la N° inserzione del mese?", min_value=1, value=1, step=1,
                                         help="Rilevante per negozi con quote gratuite.")
with col4:
    st.write("**Opzioni di Vendita:**")
    add_subtitle_input = st.checkbox("Aggiungi sottotitolo", value=False)
    use_reserve_price_input = st.checkbox("Usa prezzo di riserva (solo Aste)", value=False)
    reserve_price_val_input = st.number_input("Valore prezzo di riserva (€)", min_value=0.00, value=50.00, step=0.01, format="%.2f", disabled=not use_reserve_price_input)


st.sidebar.header("Impostazioni IVA")
apply_vat_input = st.sidebar.checkbox("Applica IVA sulle commissioni eBay", value=True)
vat_rate_val_input = st.sidebar.number_input("Aliquota IVA (%)", min_value=0.0, value=22.0, step=0.1, format="%.1f", disabled=not apply_vat_input)


if st.sidebar.button("🔄 Calcola Commissioni e Profitto", use_container_width=True):
    st.subheader("Risultati del Calcolo")

    # Convert specific country selections to broader categories for international fee calculation
    buyer_country_mapped = buyer_country_input
    if buyer_country_input in ["Italia", "Germania", "Francia", "Spagna", "Malta"]: # Add more Eurozone here if needed
        buyer_country_mapped = "Malta" # Use Malta to trigger "Eurozone_Sweden" for calculation as per example
    elif buyer_country_input == "Regno Unito":
        buyer_country_mapped = "Regno Unito"
    elif buyer_country_input == "Stati Uniti":
        buyer_country_mapped = "Stati Uniti" # Will map to US_Canada
    elif buyer_country_input == "Altro (Resto del Mondo)":
        buyer_country_mapped = "Resto del Mondo" # Will map to Rest_of_world
    # This mapping is simplistic. A robust solution would map many countries to the fee groups.


    fees = calculate_fees(
        item_price=item_price_input,
        shipping_cost=shipping_cost_input,
        item_cost=item_cost_input,
        category_id=category_id_input,
        buyer_country=buyer_country_mapped, # Use the mapped country
        seller_status=seller_status_input,
        high_inad_surcharge=high_inad_input,
        store_subscription=store_subscription_input,
        num_listings_this_month=num_listings_input,
        listing_type=listing_type_input.replace("Compralo Subito", "buy_it_now"), # Match JSON keys
        add_subtitle=add_subtitle_input,
        reserve_price_value=reserve_price_val_input,
        use_reserve_price=use_reserve_price_input,
        apply_vat=apply_vat_input,
        vat_rate_input=vat_rate_val_input
    )

    res_col1, res_col2 = st.columns(2)

    with res_col1:
        st.metric("💰 Prezzo Totale Vendita", f"{fees['total_sale_price']:.2f} €")
        st.markdown(f"<small>Prezzo oggetto: {item_price_input:.2f} € + Spedizione: {shipping_cost_input:.2f} €</small>", unsafe_allow_html=True)

        st.markdown("---")
        st.markdown(f"**Commissione sul Valore Finale (CVF)**")
        st.markdown(f"<small><i>{fees['fvf_calculation_details']} ({fees['fvf_group_name']})</i></small>", unsafe_allow_html=True)
        st.markdown(f"CVF Base: **{fees['base_fvf_amount_raw']:.2f} €**")

        if fees['fvf_discounts_surcharges']:
            for item in fees['fvf_discounts_surcharges']:
                sign = "+" if item['amount'] >= 0 else ""
                st.markdown(f"{item['name']} ({item['rate_on_fvf']:.1f}% su CVF Base): {sign}{item['amount']:.2f} €")
        
        st.markdown(f"CVF Effettiva: **{fees['final_value_fee']:.2f} €**")
        st.markdown("---")

        st.markdown(f"**Altre Commissioni sull'Ordine:**")
        st.metric("Tariffa Adeguamento Normativo", f"{fees['regulatory_fee']:.2f} €", delta_color="inverse")
        st.metric("Tariffa Internazionale", f"{fees['international_fee']:.2f} €", delta_color="inverse")
        st.markdown(f"<small><i>{fees['international_fee_details']}</i></small>", unsafe_allow_html=True)
        st.metric("Commissione Fissa per Ordine", f"{fees['fixed_order_fee']:.2f} €", delta_color="inverse")

        st.markdown("---")
        st.markdown(f"**Commissioni di Inserzione e Opzioni:**")
        st.metric("Tariffa di Inserzione", f"{fees['insertion_fee']:.2f} €", delta_color="inverse")
        st.markdown(f"<small><i>{fees['insertion_fee_details']}</i></small>", unsafe_allow_html=True)
        if fees['listing_upgrades_fees']:
            st.markdown("Opzioni di vendita:")
            for upgrade in fees['listing_upgrades_fees']:
                 st.markdown(f"- {upgrade['name']}: {upgrade['fee']:.2f} €")
        st.metric("Totale Opzioni Vendita", f"{fees['listing_upgrade_total_fee']:.2f} €", delta_color="inverse")


    with res_col2:
        st.error(f"📉 Totale Commissioni (IVA esclusa): {fees['total_fees_pre_vat']:.2f} €")
        if apply_vat_input:
            st.error(f"➕ IVA ({vat_rate_val_input:.1f}%) sulle commissioni: {fees['vat_amount']:.2f} €")
            st.error(f"📉 Totale Commissioni (IVA inclusa): {fees['total_fees_incl_vat']:.2f} €")
        
        st.markdown("---")
        st.success(f"🎯 Profitto Stimato (se IVA su commissioni è un costo): {fees['profit_if_vat_on_fees_is_cost']:.2f} €")
        st.info(f"🎯 Profitto Stimato (se IVA su commissioni è recuperabile): {fees['profit_if_vat_on_fees_reclaimed']:.2f} €")
        
        st.markdown("---")
        st.markdown("#### Dettaglio calcolo come da esempio fornito:")
        # Replicating example calculation structure
        example_fvf_base = fees['base_fvf_amount_raw']
        example_discount_amount = Decimal('0')
        for item in fees['fvf_discounts_surcharges']:
            if "Sconto Venditore Affidabilità Top" in item['name']:
                 example_discount_amount = abs(item['amount']) # abs because it's shown as positive deduction in example

        calc_line1 = f"{fees['total_sale_price']:.2f} € x {FEE_DATA['_category_map'].get(category_id_input, {}).get('variable_rate',0)*100:.1f}% = {example_fvf_base:.2f} €"
        st.text(calc_line1)
        if example_discount_amount > 0:
            discount_rate_on_fvf = FEE_DATA['discounts_surcharges']['top_rated_seller_discount_rate'] * -100 # make positive
            calc_line2 = f"{example_fvf_base:.2f} € x {discount_rate_on_fvf:.1f}% = -{example_discount_amount:.2f} €" # Use minus as it's a discount
            st.text(calc_line2)
        
        net_fvf_for_example = example_fvf_base - example_discount_amount
        st.text(f"Commissione sul valore finale (netta): {net_fvf_for_example:.2f} €")

        st.markdown("---")
        reg_fee_rate_perc = FEE_DATA['constants']['regulatory_compliance_fee_rate'] * 100
        calc_line3 = f"{fees['total_sale_price']:.2f} € x {reg_fee_rate_perc:.2f}% = {fees['regulatory_fee']:.2f} €"
        st.text("Tariffa per l'adeguamento normativo:")
        st.text(calc_line3)
        
        st.markdown("---")
        st.text(f"Tariffa internazionale: {fees['international_fee']:.2f} €")
        
        st.markdown("---")
        total_fees_per_item_example = net_fvf_for_example + fees['regulatory_fee'] + fees['international_fee']
        st.markdown(f"**Tariffe totali per oggetto (stile esempio): {total_fees_per_item_example:.2f} €**")

        st.markdown("---")
        st.text(f"Commissione fissa per ordine: {fees['fixed_order_fee']:.2f} €")
        
        total_fees_pre_vat_example_style = total_fees_per_item_example + fees['fixed_order_fee']
        # This example style sum might miss insertion/upgrade fees if they were present in the actual example image
        # The example only shows FVF, Reg, Intl, Fixed.
        st.markdown(f"**Tariffe totali (stile esempio, pre-IVA): {total_fees_pre_vat_example_style:.2f} €**")
        
        if apply_vat_input:
            vat_on_example_style = to_decimal(total_fees_pre_vat_example_style * (to_percentage_decimal(vat_rate_val_input / 100)))
            st.text(f"IVA ({vat_rate_val_input:.1f}%): {vat_on_example_style:.2f} €")
            st.markdown(f"**Tariffe totali (stile esempio, IVA inclusa): {total_fees_pre_vat_example_style + vat_on_example_style:.2f} €**")


st.sidebar.markdown("---")
st.sidebar.markdown("Disclaimer: Questo è uno strumento di stima. Le tariffe effettive di eBay potrebbero variare. Controlla sempre i termini ufficiali di eBay.")

# To run: streamlit run ebay_fee_calculator.py