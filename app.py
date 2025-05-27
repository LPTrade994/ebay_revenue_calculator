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
    # Iterate through items in the 'vehicles' dictionary
    for key, vehicle_item_data in data['vehicles'].items():
        # IMPORTANT: Check if the item is a dictionary and contains 'category_ids'
        # This handles cases like "vehicle_reserve_price_fee" which is not a dict
        if isinstance(vehicle_item_data, dict) and 'category_ids' in vehicle_item_data:
            # Ensure that 'insertion_fee' and 'final_value_fee' exist for this vehicle type
            if 'insertion_fee' in vehicle_item_data and 'final_value_fee' in vehicle_item_data:
                for cat_id in vehicle_item_data['category_ids']:
                    vehicle_cats[cat_id] = {
                        "type": key,
                        "insertion_fee": to_decimal(vehicle_item_data['insertion_fee']),
                        "final_value_fee": to_decimal(vehicle_item_data['final_value_fee']) # This is a fixed amount
                    }
            else:
                # This case should ideally not happen if the JSON for vehicle types is consistent
                st.warning(f"Dati incompleti per il tipo di veicolo '{key}' nel JSON. Mancano 'insertion_fee' o 'final_value_fee'.")
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
        price_processed_up_to = Decimal('0') # Tracks how much of the price has been processed by lower tiers
        
        # Sort tiers by their 'up_to_eur', 'from_eur', or handle 'above_eur' last
        # This simplified sort assumes 'up_to_eur' comes first, then 'from_eur'/'to_eur', then 'above_eur'
        def sort_key(tier):
            if 'up_to_eur' in tier: return (1, tier['up_to_eur'])
            if 'from_eur' in tier: return (2, tier['from_eur'])
            if 'above_eur' in tier: return (3, tier['above_eur'])
            return (4, 0) # Should not happen

        sorted_tiers = sorted(fvf_group_data['tiers'], key=sort_key)

        for tier in sorted_tiers:
            tier_rate = to_percentage_decimal(tier['rate'])
            
            if 'up_to_eur' in tier:
                tier_limit = to_decimal(tier['up_to_eur'])
                # Amount of price that falls into THIS tier's range
                # (e.g., if price is 150 and tier is up_to_100, amount_in_this_tier_range is 100)
                # (e.g., if price is 50 and tier is up_to_100, amount_in_this_tier_range is 50)
                amount_in_this_tier_range = min(total_sale_price_dec, tier_limit) - price_processed_up_to
                amount_in_this_tier_range = max(Decimal('0'), amount_in_this_tier_range) # Ensure non-negative

                calculated_fvf += amount_in_this_tier_range * tier_rate
                price_processed_up_to += amount_in_this_tier_range
                
                if total_sale_price_dec <= tier_limit: # All price processed
                    break
            
            elif 'from_eur' in tier and 'to_eur' in tier:
                tier_from = to_decimal(tier['from_eur'])
                tier_to = to_decimal(tier['to_eur'])

                # Check if the total sale price even reaches this tier's start
                if total_sale_price_dec > tier_from:
                    # The portion of the price that falls within tier_from and tier_to
                    # Max portion that can be taxed by this tier is (tier_to - tier_from)
                    # Actual portion to tax is min(price_above_tier_from, tier_span)
                    
                    # Price above the start of this tier
                    price_above_tier_from = total_sale_price_dec - tier_from
                    
                    # How much of the price actually falls into this tier's span (e.g., 100 to 990)
                    # and hasn't been processed by previous "up_to" tiers
                    
                    # This logic assumes tiers are exclusive or `price_processed_up_to` correctly reflects the boundary
                    # If an "up_to_eur" X exists, and then a "from_eur" X, we need to be careful
                    # For the "Watches" example: up_to 100, from 100 to 990, above 990.
                    # Here, from_eur 100 should mean 100.01 or that the "up_to_100" covers up to 100.00
                    # Let's assume 'from_eur' is exclusive of 'price_processed_up_to'
                    
                    # Amount of price that falls into THIS tier's specific range [tier_from, tier_to]
                    # that has not yet been taxed by prior tiers.
                    
                    # The effective start for this tier, considering what's already processed.
                    effective_start_for_this_segment = max(tier_from, price_processed_up_to)
                    
                    if total_sale_price_dec > effective_start_for_this_segment:
                        amount_in_this_tier_range = min(total_sale_price_dec, tier_to) - effective_start_for_this_segment
                        amount_in_this_tier_range = max(Decimal('0'), amount_in_this_tier_range)

                        calculated_fvf += amount_in_this_tier_range * tier_rate
                        price_processed_up_to += amount_in_this_tier_range

                        if total_sale_price_dec <= tier_to: # All relevant price processed up to this tier's end
                            break
            
            elif 'above_eur' in tier:
                tier_above = to_decimal(tier['above_eur'])
                if total_sale_price_dec > tier_above:
                    # Amount of price strictly above 'tier_above'
                    amount_in_this_tier_range = total_sale_price_dec - max(tier_above, price_processed_up_to)
                    amount_in_this_tier_range = max(Decimal('0'), amount_in_this_tier_range)
                    
                    calculated_fvf += amount_in_this_tier_range * tier_rate
                    price_processed_up_to += amount_in_this_tier_range # For completeness, though it's the last tier
                break # This must be the last tier

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
    if category_id in FEE_DATA['_vehicle_category_map']: # Check if category ID is in our pre-processed vehicle map
        vehicle_info = FEE_DATA['_vehicle_category_map'][category_id]
        if vehicle_info['type'] in ["high_value_vehicles", "motorcycles_and_others"]:
            base_fvf_amount = vehicle_info['final_value_fee'] # This is a fixed amount
            fvf_group_name = f"Veicoli ({vehicle_info['type']})"
            results['fvf_calculation_details'] = f"Tariffa fissa per {fvf_group_name}"
            is_vehicle_fixed_fvf = True
        else: # Should not happen with current JSON structure for these types if _vehicle_category_map is built correctly
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
    # Mapping from user-friendly country names to JSON keys
    country_to_fee_key_map = {
        "Italia": "Eurozone_Sweden", # Assuming sales within Italy to Eurozone sellers have 0% intl fee
        "Malta": "Eurozone_Sweden",
        "Germania": "Eurozone_Sweden",
        "Francia": "Eurozone_Sweden",
        "Spagna": "Eurozone_Sweden",
        # Add other Eurozone countries here if they are common options
        "Svezia": "Eurozone_Sweden", # Explicitly mentioned with Eurozone
        "Regno Unito": "United_Kingdom",
        "Stati Uniti": "United_States_Canada",
        "Canada": "United_States_Canada",
        # For "Europa (non Eurozona, non UK)" group
        "Svizzera": "Europe_non_eurozone_Sweden_UK",
        "Norvegia": "Europe_non_eurozone_Sweden_UK",
        # ... add other relevant non-Eurozone Europe countries
        "Altro (Resto del Mondo)": "Rest_of_world"
    }
    international_fee_rate_key = country_to_fee_key_map.get(buyer_country, "Rest_of_world") # Default to Rest_of_world
    
    international_fee_rate = to_percentage_decimal(FEE_DATA['international_fee_rates'][international_fee_rate_key])
    international_fee = to_decimal(total_sale_price_dec * international_fee_rate)
    results['international_fee'] = international_fee
    results['international_fee_details'] = f"Paese: {buyer_country}, Tariffa: {international_fee_rate*100:.1f}% ({international_fee_rate_key})"
    total_fees_pre_vat += international_fee

    # 4. Fixed Order Fee
    fixed_order_fee = to_decimal(FEE_DATA['constants']['fixed_order_fee_eur'])
    results['fixed_order_fee'] = fixed_order_fee
    total_fees_pre_vat += fixed_order_fee

    # 5. Insertion Fees
    insertion_fee = Decimal('0')
    insertion_fee_details = "Nessuna tariffa di inserzione (presupponendo quota gratuita non superata o negozio con inserzioni illimitate)"

    is_vehicle_insertion = False
    if category_id in FEE_DATA['_vehicle_category_map']:
        vehicle_info_insert = FEE_DATA['_vehicle_category_map'][category_id]
        # Check if this vehicle type has a specific insertion fee defined (it should, based on JSON structure)
        if 'insertion_fee' in vehicle_info_insert:
            insertion_fee = vehicle_info_insert['insertion_fee']
            insertion_fee_details = f"Tariffa di inserzione fissa per veicoli ({vehicle_info_insert['type']})"
            is_vehicle_insertion = True
    
    if not is_vehicle_insertion:
        current_listing_type_key = "auction" if listing_type == "Asta" else "buy_it_now"
        if store_subscription == "Nessuno":
            insertion_fee = to_decimal(FEE_DATA['insertion_fees']['non_store'][current_listing_type_key])
            insertion_fee_details = f"Tariffa inserzione '{listing_type}' senza negozio"
        else:
            store_data = FEE_DATA['insertion_fees']['store_subscriptions'][store_subscription]
            free_listings_key = f"free_{current_listing_type_key}_listings"
            extra_listing_fee_key = f"extra_listing_fee_{current_listing_type_key}"

            free_allowance = store_data.get(free_listings_key) # Use .get for safety
            if free_allowance == "unlimited":
                insertion_fee = Decimal('0')
                insertion_fee_details = f"Inserzioni '{listing_type}' illimitate con negozio {store_subscription}"
            elif isinstance(free_allowance, int) and num_listings_this_month > free_allowance:
                insertion_fee = to_decimal(store_data[extra_listing_fee_key])
                insertion_fee_details = f"Tariffa inserzione extra '{listing_type}' con negozio {store_subscription} (superata quota gratuita di {free_allowance})"
            elif isinstance(free_allowance, int): # Within free allowance
                insertion_fee = Decimal('0')
                insertion_fee_details = f"Inserzione '{listing_type}' gratuita con negozio {store_subscription} (rientra nella quota di {free_allowance})"
            else: # free_allowance might be missing or not an int/unlimited
                insertion_fee = to_decimal(store_data.get(extra_listing_fee_key, '0')) # Default to 0 if key missing
                insertion_fee_details = f"Tariffa inserzione '{listing_type}' con negozio {store_subscription} (verifica dettagli quota)"

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

    if use_reserve_price and reserve_price_value > 0 and listing_type == "Asta": # Reserve price only for auctions
        # Check if it's a vehicle category that might use the specific vehicle reserve price fee
        is_vehicle_cat_for_reserve = category_id in FEE_DATA['_vehicle_category_map']

        if is_vehicle_cat_for_reserve and "vehicle_reserve_price_fee" in FEE_DATA['vehicles']:
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

        results['listing_upgrades_fees'].append({"name": f"Prezzo di riserva ({reserve_fee_detail})", "fee": reserve_fee})
        listing_upgrade_total_fee += reserve_fee
        
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

    results['profit_if_vat_on_fees_is_cost'] = total_sale_price_dec - item_cost_dec - results['total_fees_incl_vat']
    results['profit_if_vat_on_fees_reclaimed'] = total_sale_price_dec - item_cost_dec - results['total_fees_pre_vat']
    
    return results

# --- Streamlit UI ---
st.set_page_config(page_title="Calcolatore Commissioni eBay", layout="wide")
st.title("📊 Calcolatore Commissioni eBay (Italia)")
st.caption(f"Basato sulle tariffe professionali del: {FEE_DATA['generated_on']}")

st.sidebar.header("Parametri della Vendita")

col1, col2 = st.sidebar.columns(2)

with col1:
    item_price_input = st.number_input("Prezzo dell'oggetto (€)", min_value=0.01, value=274.90, step=0.01, format="%.2f")
    shipping_cost_input = st.number_input("Costo di spedizione (€)", min_value=0.00, value=14.99, step=0.01, format="%.2f")
    item_cost_input = st.number_input("Costo di acquisto dell'oggetto (€)", min_value=0.00, value=150.00, step=0.01, format="%.2f", help="Il costo che hai sostenuto per l'oggetto.")
    
    # Create a list of (display_name, category_id) for the selectbox
    # For simplicity, manually listing some common categories and the example one.
    # A more robust solution would parse all category IDs and their names if available.
    example_category_id = 171485 # Console (PlayStation Portal example)
    category_options = {
        "Console (PlayStation Portal - Esempio)": 171485,
        "Moto: ricambi e accessori (generico)": 131090,
        "Auto: ricambi e accessori (generico)": 6030,
        "Pneumatici e cerchi (generico)": 33743,
        "Casa, arredamento e bricolage (generico)": 11700,
        "Giardino e arredamento da esterni (generico)": 159912,
        "Informatica (generico)": 58058, # Tech_accessories
        "Telefonia fissa e mobile (generico)": 15032, # Tech_accessories
        "Collezionismo (generico)": 1,
        "Orologi e gioielli (Orologi)": 260325,
        "Abbigliamento e accessori (generico)": 11450,
        "Altro (specificare ID)": 0 # Placeholder for manual ID input
    }
    selected_category_name = st.selectbox("Categoria Oggetto", options=list(category_options.keys()), index=0)
    
    if category_options[selected_category_name] == 0:
        category_id_input = st.number_input("ID Categoria eBay (se 'Altro')", min_value=1, value=example_category_id, help="Trova l'ID su eBay.")
    else:
        category_id_input = category_options[selected_category_name]
        st.caption(f"ID Categoria selezionato: {category_id_input}")


with col2:
    buyer_country_options = [
        "Italia", "Malta", "Germania", "Francia", "Spagna", "Svezia", # Eurozone_Sweden
        "Regno Unito", # United_Kingdom
        "Stati Uniti", "Canada", # United_States_Canada
        "Svizzera", "Norvegia", # Europe_non_eurozone_Sweden_UK
        "Altro (Resto del Mondo)" # Rest_of_world
    ]
    buyer_country_input = st.selectbox("Paese acquirente",
                                       options=buyer_country_options,
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
    
    # Determine default num_listings based on store type for help text
    default_listings_for_help = 1
    if store_subscription_input != "Nessuno":
        store_data_help = FEE_DATA['insertion_fees']['store_subscriptions'][store_subscription_input]
        listing_key_help = f"free_{('auction' if listing_type_input == 'Asta' else 'buy_it_now')}_listings"
        free_allowance_help = store_data_help.get(listing_key_help)
        if isinstance(free_allowance_help, int):
            default_listings_for_help = free_allowance_help + 1 # To show fee calculation beyond free
        elif free_allowance_help == "unlimited":
            default_listings_for_help = 1


    num_listings_input = st.number_input(f"Questa è la N° inserzione '{listing_type_input}' del mese?", 
                                         min_value=1, value=default_listings_for_help, step=1,
                                         help="Rilevante per negozi con quote gratuite. Inserisci un numero superiore alla quota per vedere la tariffa extra.")
with col4:
    st.write("**Opzioni di Vendita:**")
    add_subtitle_input = st.checkbox("Aggiungi sottotitolo", value=False)
    use_reserve_price_input = st.checkbox("Usa prezzo di riserva", value=False, disabled=(listing_type_input != "Asta"))
    reserve_price_val_input = st.number_input("Valore prezzo di riserva (€)", min_value=0.00, value=50.00, step=0.01, format="%.2f", disabled=not use_reserve_price_input or (listing_type_input != "Asta"))


st.sidebar.header("Impostazioni IVA")
apply_vat_input = st.sidebar.checkbox("Applica IVA sulle commissioni eBay", value=True)
vat_rate_val_input = st.sidebar.number_input("Aliquota IVA (%)", min_value=0.0, value=22.0, step=0.1, format="%.1f", disabled=not apply_vat_input)


if st.sidebar.button("🔄 Calcola Commissioni e Profitto", use_container_width=True):
    st.subheader("Risultati del Calcolo")
    
    actual_listing_type_key = "Asta" if listing_type_input == "Asta" else "Compralo Subito" # For passing to calc

    fees = calculate_fees(
        item_price=item_price_input,
        shipping_cost=shipping_cost_input,
        item_cost=item_cost_input,
        category_id=category_id_input,
        buyer_country=buyer_country_input, 
        seller_status=seller_status_input,
        high_inad_surcharge=high_inad_input,
        store_subscription=store_subscription_input,
        num_listings_this_month=num_listings_input,
        listing_type=actual_listing_type_key,
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
        st.markdown("#### Riepilogo stile Esempio (solo FVF, Reg, Intl, Fisso):")
        example_fvf_base = fees['base_fvf_amount_raw']
        example_discount_amount = Decimal('0')
        if not is_vehicle_fixed_fvf: # Check if the FVF was from a fixed vehicle fee
            for item in fees['fvf_discounts_surcharges']:
                if "Sconto Venditore Affidabilità Top" in item['name']:
                     example_discount_amount = abs(item['amount']) 
        
        # Get the FVF rate for display if not tiered and not fixed vehicle
        fvf_rate_display = "N/A"
        current_fvf_group_data = FEE_DATA['_category_map'].get(category_id_input)
        if current_fvf_group_data and 'variable_rate' in current_fvf_group_data and not is_vehicle_fixed_fvf:
             fvf_rate_display = f"{current_fvf_group_data['variable_rate']*100:.1f}%"
        elif is_vehicle_fixed_fvf:
            fvf_rate_display = "Fissa Veicolo"
        elif current_fvf_group_data and 'tiers' in current_fvf_group_data:
            fvf_rate_display = "A Scaglioni"


        calc_line1 = f"{fees['total_sale_price']:.2f} € x {fvf_rate_display} = {example_fvf_base:.2f} €"
        st.text(f"CVF Base ({fees['fvf_group_name']}):")
        st.text(calc_line1)

        if example_discount_amount > 0 and not is_vehicle_fixed_fvf:
            discount_rate_on_fvf_display = FEE_DATA['discounts_surcharges']['top_rated_seller_discount_rate'] * -100 
            calc_line2 = f"{example_fvf_base:.2f} € x {discount_rate_on_fvf_display:.1f}% = -{example_discount_amount:.2f} €"
            st.text("Sconto Venditore Affidabilità Top:")
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
        st.markdown(f"**Tariffe totali (stile esempio, pre-IVA): {total_fees_pre_vat_example_style:.2f} €**")
        
        if apply_vat_input:
            vat_on_example_style = to_decimal(total_fees_pre_vat_example_style * (to_percentage_decimal(vat_rate_val_input / 100)))
            st.text(f"IVA ({vat_rate_val_input:.1f}%): {vat_on_example_style:.2f} €")
            st.markdown(f"**Tariffe totali (stile esempio, IVA inclusa): {total_fees_pre_vat_example_style + vat_on_example_style:.2f} €**")


st.sidebar.markdown("---")
st.sidebar.markdown("Disclaimer: Questo è uno strumento di stima. Le tariffe effettive di eBay potrebbero variare. Controlla sempre i termini ufficiali di eBay.")

# To run: streamlit run ebay_fee_calculator.py