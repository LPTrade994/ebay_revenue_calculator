import streamlit as st
import json
from decimal import Decimal, ROUND_HALF_UP

# --- Streamlit Page Configuration (MUST BE THE FIRST STREAMLIT COMMAND) ---
st.set_page_config(page_title="Calcolatore Utile Netto eBay", layout="wide")

# --- Utility Functions ---
def to_decimal(value, precision='0.01'):
    return Decimal(str(value)).quantize(Decimal(precision), rounding=ROUND_HALF_UP)

def to_percentage_decimal(value):
    return Decimal(str(value))

# --- Load Fee Data ---
@st.cache_data
def load_fee_data(file_path="ebay_professional_fees_it.json"):
    with open(file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    category_to_fvf_group = {}
    for group in data['final_value_fees']:
        for cat_id in group['category_ids']:
            category_to_fvf_group[cat_id] = group
    data['_category_map'] = category_to_fvf_group

    vehicle_cats = {}
    for key, vehicle_item_data in data['vehicles'].items():
        if isinstance(vehicle_item_data, dict) and 'category_ids' in vehicle_item_data:
            if 'insertion_fee' in vehicle_item_data and 'final_value_fee' in vehicle_item_data:
                for cat_id in vehicle_item_data['category_ids']:
                    vehicle_cats[cat_id] = {
                        "type": key,
                        "insertion_fee": to_decimal(vehicle_item_data['insertion_fee']),
                        "final_value_fee": to_decimal(vehicle_item_data['final_value_fee'])
                    }
            else:
                st.warning(f"Dati incompleti per il tipo di veicolo '{key}' nel JSON.")
    data['_vehicle_category_map'] = vehicle_cats
    return data

FEE_DATA = load_fee_data()

# --- Calculation Functions ---

def get_final_value_fee_rate_and_group(category_id, total_sale_price):
    total_sale_price_dec = to_decimal(total_sale_price)
    if category_id in FEE_DATA['_vehicle_category_map']:
        vehicle_info = FEE_DATA['_vehicle_category_map'][category_id]
        if vehicle_info['type'] in ["high_value_vehicles", "motorcycles_and_others"]:
            return vehicle_info['final_value_fee'], f"Veicoli ({vehicle_info['type']})", False 
    fvf_group_data = FEE_DATA['_category_map'].get(category_id)
    if not fvf_group_data:
        st.warning(f"ID Cat. {category_id} non trovato, default 'Altre cat.'")
        for group in FEE_DATA['final_value_fees']:
            if group['group'] == "Other_categories_including_clothing_beauty":
                fvf_group_data = group; break
        if not fvf_group_data: return Decimal('0'), "Cat. non trovata", False
    group_name = fvf_group_data['group']
    if 'variable_rate' in fvf_group_data:
        return total_sale_price_dec * to_percentage_decimal(fvf_group_data['variable_rate']), group_name, False
    elif 'tiers' in fvf_group_data:
        calculated_fvf = Decimal('0'); price_processed_up_to = Decimal('0')
        def sort_key(t): return (1,t['up_to_eur']) if 'up_to_eur' in t else ((2,t['from_eur']) if 'from_eur' in t else ((3,t['above_eur']) if 'above_eur' in t else (4,0)))
        for tier in sorted(fvf_group_data['tiers'], key=sort_key):
            tier_rate = to_percentage_decimal(tier['rate'])
            if 'up_to_eur' in tier:
                tier_limit = to_decimal(tier['up_to_eur'])
                amt = max(Decimal('0'), min(total_sale_price_dec, tier_limit) - price_processed_up_to)
                calculated_fvf += amt * tier_rate; price_processed_up_to += amt
                if total_sale_price_dec <= tier_limit: break
            elif 'from_eur' in tier and 'to_eur' in tier:
                tier_from = to_decimal(tier['from_eur']); tier_to = to_decimal(tier['to_eur'])
                if total_sale_price_dec > tier_from:
                    eff_start = max(tier_from, price_processed_up_to)
                    if total_sale_price_dec > eff_start:
                        amt = max(Decimal('0'), min(total_sale_price_dec, tier_to) - eff_start)
                        calculated_fvf += amt * tier_rate; price_processed_up_to += amt
                        if total_sale_price_dec <= tier_to: break
            elif 'above_eur' in tier:
                tier_above = to_decimal(tier['above_eur'])
                if total_sale_price_dec > tier_above:
                    amt = max(Decimal('0'), total_sale_price_dec - max(tier_above, price_processed_up_to))
                    calculated_fvf += amt * tier_rate
                break 
        return calculated_fvf, group_name, True
    return Decimal('0'), "Sconosciuto", False

def calculate_fees(item_price, shipping_charged_to_customer, item_cost, your_actual_shipping_cost, # NUOVO PARAMETRO
                   category_id, buyer_country, seller_status, high_inad_surcharge, 
                   store_subscription, num_listings_this_month, listing_type, 
                   add_subtitle, reserve_price_value, use_reserve_price,
                   apply_vat, vat_rate_input):
    results = {}
    total_fees_pre_vat = Decimal('0')

    item_price_dec = to_decimal(item_price)
    shipping_charged_dec = to_decimal(shipping_charged_to_customer) # Questo è ciò che il cliente paga per la spedizione
    item_cost_dec = to_decimal(item_cost)
    your_actual_shipping_cost_dec = to_decimal(your_actual_shipping_cost) # NUOVO: costo reale spedizione

    total_sale_price_dec = item_price_dec + shipping_charged_dec # Le commissioni eBay si basano su questo
    results['total_sale_price'] = total_sale_price_dec
    results['item_cost'] = item_cost_dec
    results['your_actual_shipping_cost'] = your_actual_shipping_cost_dec # Memorizza per display

    is_vehicle_fixed_fvf = False 
    if category_id in FEE_DATA['_vehicle_category_map']:
        vehicle_info = FEE_DATA['_vehicle_category_map'][category_id]
        if vehicle_info['type'] in ["high_value_vehicles", "motorcycles_and_others"]:
            base_fvf_amount = vehicle_info['final_value_fee']; fvf_group_name = f"Veicoli ({vehicle_info['type']})"
            results['fvf_calculation_details'] = f"Tariffa fissa per {fvf_group_name}"; is_vehicle_fixed_fvf = True
        else: 
            base_fvf_amount, fvf_group_name, _ = get_final_value_fee_rate_and_group(category_id, total_sale_price_dec)
            results['fvf_calculation_details'] = f"Tariffa {'a scaglioni' if _ else 'variabile'} per '{fvf_group_name}'" # _ is is_tiered
    else:
        base_fvf_amount, fvf_group_name, _ = get_final_value_fee_rate_and_group(category_id, total_sale_price_dec)
        results['fvf_calculation_details'] = f"Tariffa {'a scaglioni' if _ else 'variabile'} per '{fvf_group_name}'"

    base_fvf_amount = to_decimal(base_fvf_amount)
    results['base_fvf_amount_raw'] = base_fvf_amount; results['fvf_group_name'] = fvf_group_name
    results['is_vehicle_fixed_fvf'] = is_vehicle_fixed_fvf

    effective_fvf = base_fvf_amount; results['fvf_discounts_surcharges'] = []
    if not is_vehicle_fixed_fvf:
        if seller_status == "Venditore Affidabilità Top":
            disc_rate = to_percentage_decimal(FEE_DATA['discounts_surcharges']['top_rated_seller_discount_rate'])
            disc_amt = base_fvf_amount * abs(disc_rate); effective_fvf -= disc_amt
            results['fvf_discounts_surcharges'].append({"name": "Sconto Venditore Affidabilità Top","rate_on_fvf": abs(disc_rate)*100,"amount": -disc_amt})
        if high_inad_surcharge:
            sur_rate = to_percentage_decimal(FEE_DATA['discounts_surcharges']['high_INAD_surcharge_rate'])
            sur_amt = base_fvf_amount * sur_rate; effective_fvf += sur_amt
            results['fvf_discounts_surcharges'].append({"name": "Sovraccarico per controversie 'Non conforme'","rate_on_fvf": sur_rate*100,"amount": sur_amt})
        if seller_status == "Sotto lo standard":
            sur_rate = to_percentage_decimal(FEE_DATA['discounts_surcharges']['below_standard_surcharge_rate'])
            sur_amt = base_fvf_amount * sur_rate; effective_fvf += sur_amt
            results['fvf_discounts_surcharges'].append({"name": "Sovraccarico Venditore Sotto lo Standard","rate_on_fvf": sur_rate*100,"amount": sur_amt})
    
    results['final_value_fee'] = to_decimal(effective_fvf); total_fees_pre_vat += results['final_value_fee']
    results['regulatory_fee'] = to_decimal(total_sale_price_dec * to_percentage_decimal(FEE_DATA['constants']['regulatory_compliance_fee_rate'])); total_fees_pre_vat += results['regulatory_fee']
    
    country_map = {"Italia":"Eurozone_Sweden","Malta":"Eurozone_Sweden","Germania":"Eurozone_Sweden","Francia":"Eurozone_Sweden","Spagna":"Eurozone_Sweden","Svezia":"Eurozone_Sweden",
                   "Regno Unito":"United_Kingdom","Stati Uniti":"United_States_Canada","Canada":"United_States_Canada","Svizzera":"Europe_non_eurozone_Sweden_UK",
                   "Norvegia":"Europe_non_eurozone_Sweden_UK","Altro (Resto del Mondo)":"Rest_of_world"}
    intl_key = country_map.get(buyer_country, "Rest_of_world")
    intl_rate = to_percentage_decimal(FEE_DATA['international_fee_rates'][intl_key])
    results['international_fee'] = to_decimal(total_sale_price_dec * intl_rate); total_fees_pre_vat += results['international_fee']
    results['international_fee_details'] = f"Paese: {buyer_country}, Tariffa: {intl_rate*100:.1f}% ({intl_key})"
    results['fixed_order_fee'] = to_decimal(FEE_DATA['constants']['fixed_order_fee_eur']); total_fees_pre_vat += results['fixed_order_fee']

    insertion_fee = Decimal('0'); insertion_fee_details = "N/A"; is_vehicle_insertion = False
    if category_id in FEE_DATA['_vehicle_category_map']:
        vehicle_info_insert = FEE_DATA['_vehicle_category_map'][category_id]
        if 'insertion_fee' in vehicle_info_insert:
            insertion_fee = vehicle_info_insert['insertion_fee']
            insertion_fee_details = f"Fissa veicoli ({vehicle_info_insert['type']})"; is_vehicle_insertion = True
    if not is_vehicle_insertion:
        key = "auction" if listing_type=="Asta" else "buy_it_now"
        if store_subscription=="Nessuno":
            insertion_fee = to_decimal(FEE_DATA['insertion_fees']['non_store'][key])
            insertion_fee_details = f"'{listing_type}' no negozio"
        else:
            store = FEE_DATA['insertion_fees']['store_subscriptions'][store_subscription]
            free_key = f"free_{key}_listings"; extra_key = f"extra_listing_fee_{key}"
            allowance = store.get(free_key)
            if allowance=="unlimited": insertion_fee_details = f"Illimitate '{listing_type}' ({store_subscription})"
            elif isinstance(allowance,int) and num_listings_this_month > allowance:
                insertion_fee=to_decimal(store[extra_key]); insertion_fee_details=f"Extra '{listing_type}' ({store_subscription}, >{allowance})"
            elif isinstance(allowance,int): insertion_fee_details=f"Gratuita '{listing_type}' ({store_subscription}, quota {allowance})"
            else: insertion_fee=to_decimal(store.get(extra_key,'0')); insertion_fee_details=f"'{listing_type}' ({store_subscription})"
    results['insertion_fee']=insertion_fee; results['insertion_fee_details']=insertion_fee_details; total_fees_pre_vat+=insertion_fee
    
    results['listing_upgrades_fees']=[]; upgrade_total=Decimal('0')
    if add_subtitle:
        sub_fee=to_decimal(FEE_DATA['listing_upgrades']['subtitle']); results['listing_upgrades_fees'].append({"name":"Sottotitolo","fee":sub_fee}); upgrade_total+=sub_fee
    if use_reserve_price and reserve_price_value > 0 and listing_type=="Asta":
        is_veh_reserve = category_id in FEE_DATA['_vehicle_category_map']
        if is_veh_reserve and "vehicle_reserve_price_fee" in FEE_DATA['vehicles']:
            res_fee=to_decimal(FEE_DATA['vehicles']['vehicle_reserve_price_fee']); res_detail=f"Fissa veicoli: {res_fee}€"
        else: 
            rp_cfg=FEE_DATA['listing_upgrades']['reserve_price']; res_val=to_decimal(reserve_price_value)
            res_fee=max(to_decimal(rp_cfg['min_fee']),min(to_decimal(rp_cfg['max_fee']),res_val*to_percentage_decimal(rp_cfg['percentage_rate'])))
            res_detail=f"{rp_cfg['percentage_rate']*100}% su {res_val}€ (min {rp_cfg['min_fee']}€, max {rp_cfg['max_fee']}€)"
        results['listing_upgrades_fees'].append({"name":f"Riserva ({res_detail})","fee":res_fee}); upgrade_total+=res_fee
    results['listing_upgrade_total_fee']=upgrade_total; total_fees_pre_vat+=upgrade_total

    results['total_fees_pre_vat'] = to_decimal(total_fees_pre_vat); vat_amount = Decimal('0')
    if apply_vat:
        vat_amount = to_decimal(results['total_fees_pre_vat'] * to_percentage_decimal(vat_rate_input/100))
    results['vat_amount']=vat_amount; results['total_fees_incl_vat']=results['total_fees_pre_vat']+vat_amount
    
    # AGGIORNAMENTO CALCOLO PROFITTO
    results['net_profit'] = total_sale_price_dec - item_cost_dec - your_actual_shipping_cost_dec - results['total_fees_incl_vat']
    results['profit_if_vat_reclaimed'] = total_sale_price_dec - item_cost_dec - your_actual_shipping_cost_dec - results['total_fees_pre_vat']
    
    return results

# --- Streamlit UI ---
st.title("💰 Calcolatore Utile Netto Vendite eBay")
st.caption(f"Basato su tariffe professionali del: {FEE_DATA['generated_on']}")

st.sidebar.header("Dati della Vendita e Costi") # Titolo sidebar aggiornato
col1, col2 = st.sidebar.columns(2)

with col1:
    item_price_input = st.number_input("Prezzo oggetto (€)", min_value=0.01, value=274.90, step=0.01, format="%.2f")
    # Rinominato per chiarezza che è quanto PAGA il cliente
    shipping_charged_input = st.number_input("Spedizione pagata dal cliente (€)", min_value=0.00, value=14.99, step=0.01, format="%.2f")
    item_cost_input = st.number_input("Tuo costo acquisto oggetto (€)", min_value=0.00, value=150.00, step=0.01, format="%.2f")
    # NUOVO INPUT per il costo di spedizione sostenuto dal venditore
    your_shipping_cost_input = st.number_input("Tuo costo spedizione al cliente (€)", min_value=0.00, value=7.00, step=0.01, format="%.2f", help="Quanto paghi effettivamente per spedire.")
    
with col2:
    example_category_id = 171485 
    category_options = {
        "Console (Esempio PS Portal)": 171485, "Moto: ricambi": 131090, "Auto: ricambi": 6030,
        "Pneumatici/Cerchi": 33743, "Casa/Arredo/Brico": 11700, "Giardino/Esterni": 159912,
        "Informatica": 58058, "Telefonia": 15032, "Collezionismo": 1, "Orologi": 260325,
        "Abbigliamento": 11450, "Altro (specificare ID)": 0
    }
    selected_category_name = st.selectbox("Categoria Oggetto", options=list(category_options.keys()), index=0)
    category_id_input = category_options[selected_category_name]
    if category_id_input == 0:
        category_id_input = st.number_input("ID Cat. eBay (se 'Altro')", min_value=1, value=example_category_id)
    else:
        st.caption(f"ID Cat.: {category_id_input}")

    buyer_country_options = ["Italia","Malta","Germania","Francia","Spagna","Svezia","Regno Unito","Stati Uniti","Canada","Svizzera","Norvegia","Altro (Resto del Mondo)"]
    buyer_country_input = st.selectbox("Paese acquirente", options=buyer_country_options, index=1)
    seller_status_input = st.selectbox("Stato venditore", ["Standard", "Venditore Affidabilità Top", "Sotto lo standard"], index=1)
    high_inad_input = st.checkbox("Alto tasso INAD?", value=False)

st.sidebar.header("Opzioni Inserzione e Negozio")
col3, col4 = st.sidebar.columns(2)
with col3:
    store_subscription_input = st.selectbox("Negozio eBay", ["Nessuno"] + list(FEE_DATA['insertion_fees']['store_subscriptions'].keys()), index=0)
    listing_type_input = st.radio("Tipo Inserzione", ["Compralo Subito", "Asta"], index=0, horizontal=True)
    default_listings_val = 1
    if store_subscription_input != "Nessuno":
        store_data_h = FEE_DATA['insertion_fees']['store_subscriptions'][store_subscription_input]
        listing_key_h = f"free_{('auction' if listing_type_input == 'Asta' else 'buy_it_now')}_listings"
        allowance_h = store_data_h.get(listing_key_h)
        if isinstance(allowance_h, int): default_listings_val = allowance_h + 1
    num_listings_input = st.number_input(f"N° inserz. '{listing_type_input}' mese?", min_value=1, value=default_listings_val, step=1)
with col4:
    st.write("**Opzioni Vendita:**"); add_subtitle_input = st.checkbox("Sottotitolo", value=False)
    is_auction = listing_type_input == "Asta"
    use_reserve_price_input = st.checkbox("Prezzo di riserva", value=False, disabled=not is_auction)
    reserve_price_val_input = st.number_input("Valore riserva (€)", min_value=0.00, value=50.00, disabled=not (use_reserve_price_input and is_auction), format="%.2f", step=0.01)

st.sidebar.header("Impostazioni IVA su Commissioni")
apply_vat_input = st.sidebar.checkbox("Applica IVA su commissioni eBay", value=True)
vat_rate_val_input = st.sidebar.number_input("Aliquota IVA (%)", min_value=0.0, value=22.0, disabled=not apply_vat_input, format="%.1f", step=0.1)

if st.sidebar.button("💰 Calcola Utile Netto", use_container_width=True):
    fees = calculate_fees(
        item_price_input, shipping_charged_input, item_cost_input, your_shipping_cost_input, # NUOVO VALORE PASSATO
        category_id_input, buyer_country_input, seller_status_input, high_inad_input,
        store_subscription_input, num_listings_input, 
        "Asta" if listing_type_input == "Asta" else "Compralo Subito",
        add_subtitle_input, reserve_price_val_input, use_reserve_price_input,
        apply_vat_input, vat_rate_val_input
    )

    st.subheader("📊 Riepilogo Utile Netto Estimato")
    st.metric(label="💸 UTILE NETTO STIMATO", value=f"{fees['net_profit']:.2f} €")
    
    # AGGIORNAMENTO SCOMPOSIZIONE UTILE
    profit_col1, profit_col2, profit_col3, profit_col4 = st.columns(4) # Aggiunta una colonna
    with profit_col1:
        st.metric(label="➕ Ricavo Totale Vendita", value=f"{fees['total_sale_price']:.2f} €")
    with profit_col2:
        st.metric(label="➖ Tuo Costo Oggetto", value=f"{fees['item_cost']:.2f} €", delta_color="inverse")
    with profit_col3:
        st.metric(label="➖ Tuo Costo Spedizione", value=f"{fees['your_actual_shipping_cost']:.2f} €", delta_color="inverse") # NUOVA METRICA
    with profit_col4:
        st.metric(label="➖ Tot. Comm. eBay (IVA incl.)", value=f"{fees['total_fees_incl_vat']:.2f} €", delta_color="inverse")
    
    st.caption(f"Formula: {fees['total_sale_price']:.2f}€ (Ricavo) - {fees['item_cost']:.2f}€ (Costo Oggetto) - {fees['your_actual_shipping_cost']:.2f}€ (Costo Sped.) - {fees['total_fees_incl_vat']:.2f}€ (Comm.) = {fees['net_profit']:.2f}€ (Utile)")
    
    if not apply_vat_input or fees['vat_amount'] == 0:
         st.info("L'IVA sulle commissioni non è stata applicata o è pari a zero.")
    else:
        st.info(f"Utile netto considera {fees['vat_amount']:.2f}€ IVA su comm. come costo. Se recuperabile, utile: {fees['profit_if_vat_reclaimed']:.2f}€.")
    st.markdown("---")

    st.subheader("💳 Dettaglio Commissioni eBay")
    res_col1, res_col2 = st.columns(2)
    with res_col1:
        st.markdown(f"**Comm. Valore Finale (CVF)**")
        st.markdown(f"<small><i>{fees['fvf_calculation_details']} ({fees['fvf_group_name']})</i></small>", unsafe_allow_html=True)
        st.markdown(f"CVF Base: **{fees['base_fvf_amount_raw']:.2f} €**")
        for item in fees['fvf_discounts_surcharges']: st.markdown(f"{item['name']} ({item['rate_on_fvf']:.1f}%): {('+' if item['amount']>=0 else '')}{item['amount']:.2f} €")
        st.markdown(f"CVF Effettiva: **{fees['final_value_fee']:.2f} €**"); st.markdown("---")
        st.metric("Adeguamento Normativo", f"{fees['regulatory_fee']:.2f} €", delta_color="off")
        st.metric("Tariffa Internazionale", f"{fees['international_fee']:.2f} €", delta_color="off")
        st.markdown(f"<small><i>{fees['international_fee_details']}</i></small>", unsafe_allow_html=True)
        st.metric("Comm. Fissa Ordine", f"{fees['fixed_order_fee']:.2f} €", delta_color="off")
    with res_col2:
        st.metric("Tariffa Inserzione", f"{fees['insertion_fee']:.2f} €", delta_color="off")
        st.markdown(f"<small><i>{fees['insertion_fee_details']}</i></small>", unsafe_allow_html=True)
        if fees['listing_upgrades_fees']:
            st.markdown("Opzioni vendita:")
            for upg in fees['listing_upgrades_fees']: st.markdown(f"- {upg['name']}: {upg['fee']:.2f} €")
        st.metric("Totale Opzioni", f"{fees['listing_upgrade_total_fee']:.2f} €", delta_color="off"); st.markdown("---")
        st.markdown(f"**Tot. Comm. (IVA escl.): {fees['total_fees_pre_vat']:.2f} €**")
        if apply_vat_input and fees['vat_amount'] > 0: st.markdown(f"**IVA ({vat_rate_val_input:.1f}%) su comm.: {fees['vat_amount']:.2f} €**")
        st.markdown(f"**TOTALE COMM. (IVA incl.): {fees['total_fees_incl_vat']:.2f} €**")
        
    with st.expander("🔍 Vedi riepilogo tariffe stile esempio eBay (dettaglio avanzato)"):
        # ... (questa parte rimane invariata)
        current_is_vehicle_fixed_fvf = fees['is_vehicle_fixed_fvf'] 
        example_fvf_base = fees['base_fvf_amount_raw']
        example_discount_amount = Decimal('0')
        if not current_is_vehicle_fixed_fvf:
            for item in fees['fvf_discounts_surcharges']:
                if "Sconto Venditore Affidabilità Top" in item['name']:
                     example_discount_amount = abs(item['amount']) 
        fvf_rate_display = "N/A"
        current_fvf_group_data = FEE_DATA['_category_map'].get(category_id_input)
        if current_is_vehicle_fixed_fvf: fvf_rate_display = "Fissa Veicolo"
        elif current_fvf_group_data:
            if 'variable_rate' in current_fvf_group_data: fvf_rate_display = f"{current_fvf_group_data['variable_rate']*100:.1f}%"
            elif 'tiers' in current_fvf_group_data: fvf_rate_display = "A Scaglioni"
        st.text(f"CVF Base ({fees['fvf_group_name']} - {fvf_rate_display}):"); st.text(f"{fees['total_sale_price']:.2f} € -> {example_fvf_base:.2f} €")
        if example_discount_amount > 0 and not current_is_vehicle_fixed_fvf:
            disc_rate_disp = FEE_DATA['discounts_surcharges']['top_rated_seller_discount_rate'] * -100 
            st.text(f"Sconto Top ({disc_rate_disp:.0f}% su CVF): -{example_discount_amount:.2f} €")
        net_fvf_for_example = example_fvf_base - example_discount_amount
        st.text(f"Comm. valore finale (netta): {net_fvf_for_example:.2f} €"); st.markdown("---")
        reg_fee_rate_perc = FEE_DATA['constants']['regulatory_compliance_fee_rate'] * 100
        st.text(f"Adeguamento normativo ({reg_fee_rate_perc:.2f}%): {fees['regulatory_fee']:.2f} €"); st.markdown("---")
        st.text(f"Tariffa internazionale: {fees['international_fee']:.2f} €"); st.markdown("---")
        total_fees_per_item_example = net_fvf_for_example + fees['regulatory_fee'] + fees['international_fee']
        st.markdown(f"**Tariffe totali per oggetto: {total_fees_per_item_example:.2f} €**"); st.markdown("---")
        st.text(f"Comm. fissa per ordine: {fees['fixed_order_fee']:.2f} €")
        total_fees_pre_vat_example_style = total_fees_per_item_example + fees['fixed_order_fee']
        st.markdown(f"**Tariffe totali (pre-IVA): {total_fees_pre_vat_example_style:.2f} €**")
        if apply_vat_input:
            vat_on_ex_style = to_decimal(total_fees_pre_vat_example_style * (Decimal(str(vat_rate_val_input))/100))
            st.text(f"IVA ({vat_rate_val_input:.1f}%): {vat_on_ex_style:.2f} €")
            st.markdown(f"**Tariffe totali (IVA inclusa): {total_fees_pre_vat_example_style + vat_on_ex_style:.2f} €**")

st.sidebar.markdown("---")
st.sidebar.markdown("Disclaimer: Strumento di stima. Tariffe eBay effettive possono variare.")