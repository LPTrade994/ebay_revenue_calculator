import streamlit as st
import yaml
from decimal import Decimal, ROUND_HALF_UP

# --- Funzioni di Caricamento e Utility ---
@st.cache_data # Cache data per performance
def load_fees_config():
    """Carica la configurazione delle tariffe dal file YAML."""
    # Assicurati che il file 'ebay_fees.yaml' sia nella stessa directory di app.py
    # o fornisci il percorso corretto.
    try:
        with open("ebay_fees.yaml", "r", encoding="utf-8") as f:
            return yaml.safe_load(f)['ebay_it_fees']
    except FileNotFoundError:
        st.error("Errore: File 'ebay_fees.yaml' non trovato. Assicurati che sia nella stessa cartella dell'app.")
        return None
    except Exception as e:
        st.error(f"Errore nel caricamento del file YAML: {e}")
        return None

def to_decimal(value, precision="0.01"):
    """Converte un valore in Decimal con precisione definita."""
    return Decimal(str(value)).quantize(Decimal(precision), rounding=ROUND_HALF_UP)

def get_vat_exclusive(amount_with_vat, vat_rate_decimal):
    """Calcola il valore IVA esclusa."""
    return amount_with_vat / (1 + vat_rate_decimal)

def get_vat_amount(amount_vat_exclusive, vat_rate_decimal):
    """Calcola l'importo dell'IVA."""
    return amount_vat_exclusive * vat_rate_decimal

# --- Funzioni di Calcolo Commissioni ---

def calculate_final_value_fee(category_id, total_sale_vat_excl, fees_data, seller_level, snad_rate):
    """Calcola la Commissione sul Valore Finale (FVF)."""
    category_info = fees_data['categories'].get(str(category_id))
    if not category_info:
        st.warning(f"Categoria ID {category_id} non trovata, uso la categoria 'default'.")
        category_info = fees_data['categories']['default']
        # 'default' in YAML ha 'applies_to', prendiamo i valori da lì se non direttamente
        if 'rate' not in category_info: # Se default è strutturato con applies_to
             category_info = {
                'name': "Altre categorie (default)",
                'fee_type': fees_data['categories']['default']['fee_type'],
                'rate': fees_data['categories']['default']['rate']
            }


    fvf_amount = Decimal("0.00")
    base_fvf_rate_applied = Decimal("0.00") # Per tracciare il tasso base applicato

    if category_info['fee_type'] == 'flat':
        rate = Decimal(str(category_info['rate']))
        base_fvf_rate_applied = rate
        fvf_amount = total_sale_vat_excl * rate
    elif category_info['fee_type'] == 'tier':
        tiers = category_info['tiers']
        remaining_amount = total_sale_vat_excl
        temp_fvf_total_rate_numerator = Decimal("0.00")

        for tier in sorted(tiers, key=lambda x: x.get('up_to', float('inf'))): # Ordina per 'up_to'
            tier_rate = Decimal(str(tier['rate']))
            if 'up_to' in tier:
                threshold = Decimal(str(tier['up_to']))
                if remaining_amount > 0:
                    amount_in_tier = min(remaining_amount, threshold)
                    if total_sale_vat_excl <= threshold: # Se il totale è in questo scaglione o meno
                        amount_this_tier_applies_to = total_sale_vat_excl
                        for prev_tier_idx in range(tiers.index(tier)): #sottraggo i precedenti
                            if 'up_to' in tiers[prev_tier_idx]:
                                amount_this_tier_applies_to -= Decimal(str(tiers[prev_tier_idx]['up_to']))
                            elif 'from' in tiers[prev_tier_idx] and 'to' in tiers[prev_tier_idx]:
                                amount_this_tier_applies_to -= (Decimal(str(tiers[prev_tier_idx]['to'])) - Decimal(str(tiers[prev_tier_idx]['from'])))

                        fvf_amount += max(Decimal("0"), amount_this_tier_applies_to) * tier_rate
                        temp_fvf_total_rate_numerator += max(Decimal("0"), amount_this_tier_applies_to) * tier_rate
                        remaining_amount = Decimal("0") # Finito
                        break
                    else: # Il totale supera questo scaglione
                        fvf_amount += threshold * tier_rate
                        temp_fvf_total_rate_numerator += threshold * tier_rate
                        remaining_amount -= threshold


            elif 'from' in tier and 'to' in tier:
                tier_from = Decimal(str(tier['from']))
                tier_to = Decimal(str(tier['to']))
                if total_sale_vat_excl > tier_from:
                    amount_in_this_specific_tier = min(total_sale_vat_excl, tier_to) - tier_from
                    fvf_for_this_tier = max(Decimal("0"), amount_in_this_specific_tier) * tier_rate
                    fvf_amount += fvf_for_this_tier
                    temp_fvf_total_rate_numerator += fvf_for_this_tier


            elif 'above' in tier:
                threshold = Decimal(str(tier['above']))
                if total_sale_vat_excl > threshold:
                    amount_in_tier = total_sale_vat_excl - threshold
                    fvf_amount += amount_in_tier * tier_rate
                    temp_fvf_total_rate_numerator += amount_in_tier * tier_rate

        if total_sale_vat_excl > 0:
            base_fvf_rate_applied = temp_fvf_total_rate_numerator / total_sale_vat_excl
        else:
            base_fvf_rate_applied = Decimal("0.00")


    # Applica sconti/sovraccosti del venditore alla FVF
    # Questi sono punti percentuali (pp) aggiunti/sottratti al tasso, o % sulla FVF
    effective_fvf_rate = base_fvf_rate_applied # Inizia con il tasso base calcolato (o medio per tier)
    variable_fvf_amount = fvf_amount # Per il top rated discount

    # Sovraccosti (aggiungono punti percentuali al tasso FVF)
    surcharge_pp = Decimal("0.00")
    if snad_rate == "Molto alta":
        surcharge_pp += Decimal(str(fees_data['discounts_surcharges']['high_snad_surcharge']))
    if seller_level == "Sotto lo standard":
        surcharge_pp += Decimal(str(fees_data['discounts_surcharges']['below_standard_surcharge']))

    if surcharge_pp > 0:
        additional_fvf_from_surcharges = total_sale_vat_excl * surcharge_pp
        fvf_amount += additional_fvf_from_surcharges
        # Aggiorna effective_fvf_rate per riflettere i sovraccosti
        if total_sale_vat_excl > 0 :
             effective_fvf_rate = fvf_amount / total_sale_vat_excl # Ricalcola il tasso effettivo
        else:
             effective_fvf_rate = Decimal("0.00")


    # Sconto Venditore Affidabilità Top (-10% sulla parte variabile FVF)
    # La "parte variabile" è l'intera FVF calcolata finora, dato che non ci sono parti fisse nella FVF per categoria
    if seller_level == "Affidabilità Top":
        discount_rate = Decimal(str(fees_data['discounts_surcharges']['top_rated_discount'])) # è negativo
        discount_amount = variable_fvf_amount * discount_rate # discount_amount sarà negativo
        fvf_amount += discount_amount
        # Aggiorna effective_fvf_rate per riflettere lo sconto
        if total_sale_vat_excl > 0 :
             effective_fvf_rate = fvf_amount / total_sale_vat_excl # Ricalcola il tasso effettivo
        else:
            effective_fvf_rate = Decimal("0.00")


    return fvf_amount, effective_fvf_rate # Ritorna anche il tasso effettivo per chiarezza


def calculate_insertion_fee(store_type, listing_type, is_free_listing_used, fees_data, is_vehicle=False, vehicle_type_key=None):
    """Calcola la tariffa d'inserzione."""
    if is_vehicle and vehicle_type_key:
        return Decimal(str(fees_data['vehicles'][vehicle_type_key]['insertion_fee']))

    if is_free_listing_used:
        return Decimal("0.00")

    if store_type == "Nessun negozio":
        return Decimal(str(fees_data['insertion_fees']['non_store'][listing_type]))
    else:
        store_config = fees_data['insertion_fees']['stores'][store_type.lower().replace(" ", "_").replace("+", "plus")] # es. premium_plus
        if listing_type == 'fixed_price':
            return Decimal(str(store_config['fixed_price_extra']))
        elif listing_type == 'auction':
            return Decimal(str(store_config['auction_extra']))
    return Decimal("0.00")


# --- UI e Logica Principale ---
def main():
    st.set_page_config(page_title="Calcolatore Profitto eBay", layout="wide")
    st.title("📊 Calcolatore di Profitto eBay (Italia)")
    st.markdown("Stima il tuo utile netto per le vendite su eBay.it.")

    fees_data = load_fees_config()
    if not fees_data:
        return # Interrompi se il caricamento fallisce

    # --- COLONNA INPUT ---
    col_input, col_results = st.columns(2)

    with col_input:
        st.header("📦 Dati del Prodotto e Vendita")

        vat_rate_percentage = st.number_input("Aliquota IVA applicabile (%)", min_value=0.0, value=22.0, step=0.1, format="%.1f", help="IVA standard italiana è 22%. Inserisci 0 se non applichi IVA (es. regime forfettario).")
        vat_rate = to_decimal(vat_rate_percentage / 100, "0.001") # es. 0.22

        prices_include_vat = st.radio(
            "I prezzi che inserirai di seguito sono:",
            ("IVA Inclusa", "IVA Esclusa (Imponibile)"),
            horizontal=True,
            help="Seleziona se i valori di 'Prezzo di vendita' e 'Costo di spedizione addebitato' sono comprensivi di IVA o meno. Il calcolatore userà valori IVA esclusa per le commissioni eBay (come da tariffario)."
        )
        st.info(f"""**Nota Importante:** Le tariffe eBay indicate nel tariffario e usate per i calcoli sono **IVA esclusa**.
                    Questo calcolatore convertirà i tuoi prezzi in IVA esclusa (se necessario) prima di applicare le commissioni.
                    L'IVA (se applicabile) sul prezzo di vendita è una tua responsabilità verso lo stato.
                    L'IVA sulle commissioni eBay ti verrà addebitata da eBay in fattura (se sei soggetto IVA).""")


        selling_price_input = to_decimal(st.number_input("Prezzo di vendita articolo (€)", min_value=0.01, value=50.00, step=0.01, format="%.2f"))
        shipping_charged_to_buyer_input = to_decimal(st.number_input("Costo di spedizione addebitato all'acquirente (€)", min_value=0.00, value=5.00, step=0.01, format="%.2f"))

        st.subheader("Costi Diretti (IVA Esclusa o Inclusa come sopra)")
        item_cost_input = to_decimal(st.number_input("Tuo costo di acquisto/produzione articolo (€)", min_value=0.00, value=10.00, step=0.01, format="%.2f"))
        actual_shipping_cost_input = to_decimal(st.number_input("Tuo costo effettivo di spedizione (€)", min_value=0.00, value=4.00, step=0.01, format="%.2f"))
        other_direct_costs_input = to_decimal(st.number_input("Altri costi diretti per questa vendita (es. imballaggio) (€)", min_value=0.00, value=1.00, step=0.01, format="%.2f"))

        # Conversione a IVA esclusa per calcoli interni
        if prices_include_vat == "IVA Inclusa":
            selling_price_vat_excl = get_vat_exclusive(selling_price_input, vat_rate)
            shipping_charged_to_buyer_vat_excl = get_vat_exclusive(shipping_charged_to_buyer_input, vat_rate)
            item_cost_vat_excl = get_vat_exclusive(item_cost_input, vat_rate)
            actual_shipping_cost_vat_excl = get_vat_exclusive(actual_shipping_cost_input, vat_rate)
            other_direct_costs_vat_excl = get_vat_exclusive(other_direct_costs_input, vat_rate)
        else: # IVA Esclusa
            selling_price_vat_excl = selling_price_input
            shipping_charged_to_buyer_vat_excl = shipping_charged_to_buyer_input
            item_cost_vat_excl = item_cost_input
            actual_shipping_cost_vat_excl = actual_shipping_cost_input
            other_direct_costs_vat_excl = other_direct_costs_input

        total_sale_vat_excl = selling_price_vat_excl + shipping_charged_to_buyer_vat_excl
        total_sale_vat_incl = selling_price_input + shipping_charged_to_buyer_input # Per riferimento utente

        st.markdown("---")
        st.header("🏪 Dati Venditore e Inserzione")

        # Creazione lista categorie per selectbox
        category_options = {"0": "Seleziona una categoria..."}
        for cat_id, cat_details in fees_data['categories'].items():
            if cat_id != 'default':
                category_options[cat_id] = f"{cat_details['name']} (ID: {cat_id})"
            else: # Gestione del default se strutturato in modo diverso
                if 'name' in cat_details:
                     category_options[cat_id] = cat_details['name'] # Se default ha un nome
                else: # Se default è solo 'rate' e 'fee_type'
                     category_options["default_explicit"] = "Altre categorie (Default Tariffario)"


        category_id_selected = st.selectbox(
            "Categoria eBay dell'oggetto",
            options=list(category_options.keys()),
            format_func=lambda x: category_options[x]
        )
        if category_id_selected == "default_explicit": # Mappa al 'default' reale se l'utente sceglie la nostra opzione
            category_id_for_calc = 'default'
        else:
            category_id_for_calc = category_id_selected


        is_vehicle_sale = st.checkbox("È una vendita di Veicolo (auto, moto, ecc.)?", value=False, help="Seleziona se stai vendendo un veicolo dalle categorie speciali con tariffe fisse.")
        vehicle_type_key = None
        if is_vehicle_sale:
            vehicle_category_options = {k: k.replace("_", ", ") for k in fees_data['vehicles'].keys()} # Nomi più leggibili
            vehicle_type_key = st.selectbox("Tipo di Veicolo", options=list(vehicle_category_options.keys()), format_func=lambda x: vehicle_category_options[x])


        store_type = st.selectbox(
            "Tipo di Negozio eBay",
            ("Nessun negozio", "Base", "Premium", "Premium Plus")
        )

        listing_type = 'fixed_price' # Default, poi cambiamo se asta
        if not is_vehicle_sale: # Le aste per veicoli sono gestite diversamente
             listing_format_choice = st.radio(
                "Formato di vendita:",
                ("Compralo Subito (Prezzo Fisso)", "Asta online"),
                horizontal=True,
                help="Il 'Compralo Subito' è 'fixed_price', 'Asta online' è 'auction'."
            )
             listing_type = 'fixed_price' if "Compralo Subito" in listing_format_choice else 'auction'


        is_free_listing_used = st.checkbox("Questa inserzione ha utilizzato una tariffa d'inserzione gratuita?", value=True, help="Seleziona se l'inserzione rientra nelle tue quote gratuite mensili (negozio o base).")

        seller_level = st.selectbox(
            "Livello del venditore",
            ("Affidabilità Top", "Sopra lo standard", "Sotto lo standard")
        )
        snad_rate = st.selectbox(
            "Tasso reclami per 'Oggetto non conforme' (SNAD)",
            ("Normale", "Molto alta")
        )

        st.markdown("---")
        st.header("🌍 Opzioni Internazionali e Avanzate")
        international_shipping_zone_options = {
            "eurozone_sweden": "Eurozona o Svezia (Nessuna tariffa aggiuntiva)",
            "europe_non_eurozone_sweden_uk": "Europa (non Eurozona/Svezia, escl. UK)",
            "united_kingdom": "Regno Unito",
            "rest_of_world": "Resto del Mondo"
        }
        shipping_destination_key = st.selectbox(
            "Destinazione spedizione (per tariffa internazionale)",
            options=list(international_shipping_zone_options.keys()),
            format_func=lambda x: international_shipping_zone_options[x]
        )

        currency_conversion_needed = st.checkbox("È necessaria una conversione di valuta?", value=False, help="Seleziona se il pagamento dell'acquirente è in una valuta diversa da quella del tuo accredito e eBay applica la sua commissione di conversione.")

        st.subheader("Opzioni di vendita facoltative (per questa inserzione)")
        use_subtitle = st.checkbox("Utilizzo del Sottotitolo (€"+str(fees_data['listing_upgrades']['subtitle'])+")", value=False)

        reserve_price_non_vehicle = Decimal("0.00")
        if listing_type == 'auction' and not is_vehicle_sale:
            use_reserve_price = st.checkbox("Utilizzo del Prezzo di Riserva (per aste non veicoli)?", value=False)
            if use_reserve_price:
                reserve_price_input_val = to_decimal(st.number_input("Valore del Prezzo di Riserva (€)", min_value=0.01, value=100.00, step=0.01, format="%.2f"))
                rp_config = fees_data['listing_upgrades']['reserve_price']
                calculated_rp_fee = reserve_price_input_val * Decimal(str(rp_config['rate']))
                reserve_price_non_vehicle = max(Decimal(str(rp_config['min'])), min(calculated_rp_fee, Decimal(str(rp_config['max']))))
                st.caption(f"Costo calcolato per prezzo di riserva: €{reserve_price_non_vehicle:.2f}")

        # Seconda categoria è complessa per FVF, per ora solo costo inserzione
        # use_second_category = st.checkbox("Utilizzo di una Seconda Categoria?", value=False, help="Nota: questo raddoppia la tariffa d'inserzione e si applica la FVF più alta tra le due categorie. Questo calcolatore aggiungerà solo il costo di una seconda tariffa d'inserzione base.")


    # --- COLONNA RISULTATI ---
    with col_results:
        st.header("💰 Risultati Stima Profitto")

        if category_id_selected == "0" and not is_vehicle_sale : # Categoria non selezionata e non è un veicolo
            st.warning("⚠️ Seleziona una categoria eBay per calcolare la Commissione sul Valore Finale.")
            st.stop()


        # 0. Costi diretti totali (IVA esclusa)
        total_direct_costs_vat_excl = item_cost_vat_excl + actual_shipping_cost_vat_excl + other_direct_costs_vat_excl

        # 1. Tariffa d'inserzione
        insertion_fee = calculate_insertion_fee(store_type, listing_type, is_free_listing_used, fees_data, is_vehicle_sale, vehicle_type_key)
        # if use_second_category: # Semplificato: aggiunge un'altra tariffa d'inserzione base
        #     insertion_fee += calculate_insertion_fee(store_type, listing_type, False, fees_data) # La seconda non è mai gratis

        # 2. Commissione fissa per ordine
        fixed_fee_per_order = Decimal(str(fees_data['constants']['fixed_fee_per_order']))

        # 3. Adeguamento normativo
        regulatory_adjustment_fee = total_sale_vat_excl * Decimal(str(fees_data['constants']['regulatory_adjustment_rate']))

        # 4. Commissione sul Valore Finale (FVF)
        if is_vehicle_sale and vehicle_type_key:
            fvf_amount = Decimal(str(fees_data['vehicles'][vehicle_type_key]['final_value_fee']))
            fvf_rate_effective = Decimal("0.00") # Per i veicoli è fissa
            category_name_display = f"Veicolo ({vehicle_category_options[vehicle_type_key]})"
        else:
            fvf_amount, fvf_rate_effective = calculate_final_value_fee(category_id_for_calc, total_sale_vat_excl, fees_data, seller_level, snad_rate)
            cat_info_display = fees_data['categories'].get(str(category_id_for_calc))
            if not cat_info_display : cat_info_display = fees_data['categories']['default'] # fallback if somehow not found after selection
            category_name_display = cat_info_display.get('name', 'Categoria Default')


        # 5. Tariffa internazionale
        international_fee = Decimal("0.00")
        if shipping_destination_key != "eurozone_sweden":
            international_rate = Decimal(str(fees_data['international_fee_rates'][shipping_destination_key]))
            international_fee = total_sale_vat_excl * international_rate

        # 6. Commissione per conversione valuta
        currency_conversion_fee = Decimal("0.00")
        if currency_conversion_needed:
            currency_conversion_fee = total_sale_vat_excl * Decimal(str(fees_data['constants']['currency_conversion_rate']))

        # 7. Costi opzioni di vendita
        subtitle_fee = Decimal(str(fees_data['listing_upgrades']['subtitle'])) if use_subtitle else Decimal("0.00")
        # reserve_price_non_vehicle è già calcolato sopra

        total_optional_fees = subtitle_fee + reserve_price_non_vehicle

        # Somma tutte le commissioni eBay (IVA esclusa)
        total_ebay_fees_vat_excl = (
            insertion_fee +
            fixed_fee_per_order +
            regulatory_adjustment_fee +
            fvf_amount +
            international_fee +
            currency_conversion_fee +
            total_optional_fees
        )

        # Calcolo IVA sulle commissioni eBay (l'utente la paga a eBay)
        vat_on_ebay_fees = get_vat_amount(total_ebay_fees_vat_excl, vat_rate)

        # Profitto
        net_revenue_from_sale_vat_excl = total_sale_vat_excl - total_ebay_fees_vat_excl
        net_profit_vat_excl = net_revenue_from_sale_vat_excl - total_direct_costs_vat_excl

        # Profitto Lordo (considerando l'IVA sulla vendita se l'utente la incassa)
        # e l'IVA sulle commissioni come costo aggiuntivo
        # Se l'utente è in regime forfettario, vat_rate sarà 0, quindi non cambia nulla
        # Questa è una visione più "cash flow" per chi non scarica l'IVA.
        # Per aziende in regime ordinario, l'IVA è una partita di giro.
        # Il profitto "reale" per un'azienda è quello IVA esclusa.

        # Totale incassato dall'acquirente
        total_collected_from_buyer = total_sale_vat_incl

        # Costi totali "cash"
        # Costi diretti (se input IVA inclusa, sono già a quel valore, altrimenti aggiungiamo IVA se l'utente la paga e non la scarica)
        # Per semplicità, assumiamo che i costi diretti siano "finali" per l'utente (se forfettario, paga l'IVA sui suoi acquisti e non la scarica)
        # Questa parte può diventare complessa a seconda del regime fiscale. Manteniamola semplice:
        # Profitto netto dopo aver considerato l'IVA sulle fees eBay come un costo
        # e i costi diretti come inseriti
        # L'IVA sulla vendita, se incassata, deve essere versata.

        # Profitto al netto dell'imponibile e dei costi diretti imponibili.
        # L'IVA sulla vendita è un debito verso lo stato.
        # L'IVA sulle commissioni è un credito (se si è in regime ordinario).

        st.subheader("Riepilogo Entrate e Costi (€)")
        st.metric(label="Totale Vendita (Prezzo Articolo + Spedizione all'Acquirente)", value=f"{total_sale_vat_incl:.2f} (IVA Incl.)")
        st.caption(f"Corrispondente a € {total_sale_vat_excl:.2f} IVA Esclusa")

        st.markdown("---")
        st.subheader(f"Dettaglio Commissioni eBay (IVA Esclusa - {fees_data['meta']['currency']})")

        fees_breakdown = {
            "Tariffa d'inserzione:": insertion_fee,
            f"Comm. fissa per ordine (€{fees_data['constants']['fixed_fee_per_order']}):": fixed_fee_per_order,
            f"Adeguamento normativo ({fees_data['constants']['regulatory_adjustment_rate']*100:.2f}% su totale vendita):": regulatory_adjustment_fee,
            f"Comm. Valore Finale (FVF) per '{category_name_display}' (Tasso eff. {fvf_rate_effective*100:.2f}% su tot. vendita):": fvf_amount,
            f"Tariffa internazionale ({international_shipping_zone_options[shipping_destination_key]}):": international_fee,
            f"Comm. conversione valuta (se applicabile):": currency_conversion_fee,
            "Costi opzioni vendita (sottotitolo, riserva):": total_optional_fees,
        }
        for desc, val in fees_breakdown.items():
            st.write(f"{desc:<65} € {val:.2f}")

        st.markdown("---")
        st.metric(label="🔴 Totale Commissioni eBay (IVA Esclusa)", value=f"€ {total_ebay_fees_vat_excl:.2f}")
        if vat_rate > 0:
            st.caption(f"IVA ({vat_rate_percentage}%) su commissioni eBay (da versare a eBay): € {vat_on_ebay_fees:.2f}")
            st.caption(f"Totale costo commissioni eBay (IVA Inclusa): € {(total_ebay_fees_vat_excl + vat_on_ebay_fees):.2f}")

        st.markdown("---")
        st.metric(label="🔵 Ricavo Netto dalla Vendita (dopo commissioni eBay, IVA Esclusa)", value=f"€ {net_revenue_from_sale_vat_excl:.2f}")

        st.markdown("---")
        st.subheader("Costi Diretti Sostenuti (IVA Esclusa)")
        st.write(f"Costo acquisto/produzione articolo: € {item_cost_vat_excl:.2f}")
        st.write(f"Costo spedizione effettivo: € {actual_shipping_cost_vat_excl:.2f}")
        st.write(f"Altri costi diretti: € {other_direct_costs_vat_excl:.2f}")
        st.metric(label="🟠 Totale Costi Diretti (IVA Esclusa)", value=f"€ {total_direct_costs_vat_excl:.2f}")

        st.markdown("---")
        st.markdown("<br>", unsafe_allow_html=True) # Spazio
        st.success(f"🟢 **Utile Netto Stimato (IVA Esclusa): € {net_profit_vat_excl:.2f}**")

        if vat_rate > 0 :
            profit_vat_amount = get_vat_amount(net_profit_vat_excl, vat_rate)
            st.info(f"""
            **Considerazioni sull'IVA (per venditori soggetti a IVA in regime ordinario):**
            - IVA incassata sulla vendita (da versare allo Stato): € {(total_sale_vat_incl - total_sale_vat_excl):.2f}
            - IVA pagata sulle commissioni eBay (credito IVA): € {vat_on_ebay_fees:.2f}
            - Il tuo utile netto imponibile ai fini delle imposte dirette (es. IRPEF/IRES) è € {net_profit_vat_excl:.2f}.
            L'IVA è una partita di giro.
            """)
        else:
            st.info(f"""
            **Considerazioni per venditori non soggetti a IVA (es. regime forfettario):**
            - Non incassi IVA sulla vendita e non la versi.
            - Paghi l'IVA sulle commissioni eBay come un costo (già incluso implicitamente se hai inserito i costi al lordo IVA, o da considerare se hai inserito al netto).
            - Il tuo utile netto di € {net_profit_vat_excl:.2f} è la base per il calcolo delle tue imposte (con le aliquote del tuo regime).
            Se hai inserito i costi diretti al netto dell'IVA e sei in regime forfettario, ricorda che l'IVA pagata sui tuoi acquisti è un costo indeducibile che dovresti aggiungere ai "Costi diretti".
            """)

        st.markdown("---")
        with st.expander("Dettagli Negozio (costo mensile escluso da questo calcolo per singolo oggetto)"):
            if store_type != "Nessun negozio":
                store_key = store_type.lower().replace(" ", "_").replace("+", "plus")
                store_details = fees_data['insertion_fees']['stores'][store_key]
                st.write(f"**Negozio {store_type}:**")
                st.write(f"- Costo mensile (IVA escl.): € {store_details['monthly_fee']:.2f}")
                st.write(f"- Inserzioni 'Compralo Subito' gratuite: {store_details['fixed_price_free']}")
                st.write(f"- Inserzioni 'Asta online' gratuite: {store_details['auction_free']}")
                st.write("Questo costo mensile è un overhead e va ripartito sul totale delle vendite mensili.")
            else:
                st.write("Nessun negozio selezionato. Le tariffe d'inserzione 'non negozio' si applicano dopo eventuali inserzioni gratuite base fornite da eBay.")

    st.markdown("---")
    st.caption(f"Dati tariffe aggiornati al: {fees_data['meta']['generated']}. Valuta: {fees_data['meta']['currency']}. Tutte le tariffe eBay sono IVA esclusa.")
    st.caption("Questo calcolatore è uno strumento di stima. Verifica sempre le tariffe ufficiali eBay.")

if __name__ == "__main__":
    main()