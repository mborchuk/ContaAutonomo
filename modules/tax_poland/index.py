"""
Tax Poland Module
Adapts tax calculations for Polish freelancers (JDG / B2B) in the IT sector.

Supports two taxation modes:
  - Flat tax (podatek liniowy) — 19% PIT, most common for IT
  - Progressive (skala podatkowa) — 12%/32% brackets

Overrides:
  - VAT rate → 23%
  - Income tax calculation → Polish PIT brackets or flat 19%
  - Social security label → ZUS
  - Health insurance → 4.9% of income (flat tax) or 9% (progressive)

References (content rephrased for compliance with licensing restrictions):
  - Polish PIT rates and thresholds based on publicly available tax law
  - ZUS contribution amounts based on 2025/2026 published rates
"""

from module_manager import BaseModule
from flask import render_template


# Polish tax constants (2025/2026)
VAT_RATE_PL = 23.0  # %
FLAT_TAX_RATE = 0.19  # 19%
PROGRESSIVE_BRACKETS = [
    (30000, 0.0, 'PLN 0 - 30,000 (tax-free)'),
    (120000, 0.12, 'PLN 30,000 - 120,000'),
    (float('inf'), 0.32, 'PLN 120,000+'),
]
HEALTH_RATE_FLAT = 0.049  # 4.9% for flat tax
HEALTH_RATE_PROGRESSIVE = 0.09  # 9% for progressive
ZUS_MONTHLY_FULL = 1_600.0  # approx full ZUS (społeczne) 2025/2026


class TaxPolandModule(BaseModule):

    @property
    def module_id(self):
        return 'tax_poland'

    @property
    def name(self):
        return 'Tax Poland (IT)'

    @property
    def description(self):
        return 'Polish tax rules for IT freelancers (JDG/B2B). Flat tax 19% or progressive 12%/32%, VAT 23%, ZUS.'

    @property
    def version(self):
        return '1.0.0'

    @property
    def settings_tab(self):
        return 'general'

    # ── Models ──────────────────────────────────────────────────────

    def register_models(self, db):
        self._db = db

        class TaxPolandConfig(db.Model):
            __tablename__ = 'tax_poland_config'
            __table_args__ = {'extend_existing': True}
            id = db.Column(db.Integer, primary_key=True)
            tax_mode = db.Column(db.String(20), default='flat')  # 'flat' or 'progressive'
            zus_monthly = db.Column(db.Float, default=ZUS_MONTHLY_FULL)
            health_deductible = db.Column(db.Boolean, default=True)

        self.TaxPolandConfig = TaxPolandConfig

        # Migrate existing table
        self._migrate(db)

        return {'tax_poland_config': TaxPolandConfig}

    def _migrate(self, db):
        for col, typedef in [
            ('health_deductible', 'BOOLEAN DEFAULT 1'),
        ]:
            try:
                db.session.execute(db.text(
                    f'ALTER TABLE tax_poland_config ADD COLUMN {col} {typedef}'
                ))
                db.session.commit()
            except Exception:
                db.session.rollback()

    def _get_config(self):
        cfg = self.TaxPolandConfig.query.first()
        if not cfg:
            cfg = self.TaxPolandConfig(tax_mode='flat', zus_monthly=ZUS_MONTHLY_FULL)
            self._db.session.add(cfg)
            self._db.session.commit()
        return cfg

    # ── Settings UI ─────────────────────────────────────────────────

    def get_settings_html(self, settings):
        cfg = self._get_config()
        flat_checked = 'checked' if cfg.tax_mode == 'flat' else ''
        progressive_checked = 'checked' if cfg.tax_mode == 'progressive' else ''
        return f'''
        <h3 style="margin-bottom: 15px; color: #333;">🇵🇱 Tax Poland (IT)</h3>
        <p style="color: #666; margin-bottom: 15px;">
            Polish tax settings for IT freelancers (JDG / B2B).
        </p>

        <div class="form-group" style="margin-bottom: 15px;">
            <label style="font-weight: 600; margin-bottom: 10px; display: block;">Tax Mode</label>
            <label style="display: flex; align-items: center; cursor: pointer; margin-bottom: 8px;">
                <input type="radio" name="tax_pl_mode" value="flat"
                       {flat_checked}
                       style="margin-right: 8px; width: 16px; height: 16px;">
                <span>Flat tax (podatek liniowy) — 19%</span>
            </label>
            <label style="display: flex; align-items: center; cursor: pointer;">
                <input type="radio" name="tax_pl_mode" value="progressive"
                       {progressive_checked}
                       style="margin-right: 8px; width: 16px; height: 16px;">
                <span>Progressive (skala podatkowa) — 12% / 32%</span>
            </label>
        </div>

        <div class="form-group" style="margin-bottom: 15px;">
            <label for="tax_pl_zus" style="font-weight: 600;">Monthly ZUS (PLN)</label>
            <input type="number" id="tax_pl_zus" name="tax_pl_zus"
                   value="{cfg.zus_monthly:.2f}" step="0.01" min="0"
                   style="width: 160px;">
            <small style="display: block; margin-top: 5px; color: #666;">
                Full ZUS social insurance contribution (składki społeczne). ~1,600 PLN/month in 2025/2026.
            </small>
        </div>
        '''

    def save_settings(self, settings, form):
        if 'tax_pl_mode' not in form:
            return
        cfg = self._get_config()
        cfg.tax_mode = form.get('tax_pl_mode', 'flat')
        try:
            cfg.zus_monthly = float(form.get('tax_pl_zus', ZUS_MONTHLY_FULL))
        except (ValueError, TypeError):
            cfg.zus_monthly = ZUS_MONTHLY_FULL
        self._db.session.commit()
        self.logger.info('Tax Poland settings saved: mode=%s, zus=%.2f',
                         cfg.tax_mode, cfg.zus_monthly)

    # ── On Enable: set VAT to 23% ──────────────────────────────────

    def on_enable(self):
        """Set default VAT rate to 23% when module is enabled."""
        try:
            from app import Settings
            settings = Settings.query.first()
            if settings:
                settings.default_vat_rate = VAT_RATE_PL
                self._db.session.commit()
                self.logger.info('Tax Poland: set VAT rate to %.1f%%', VAT_RATE_PL)
        except Exception as e:
            self.logger.error('Tax Poland: failed to set VAT rate: %s', e)

    # ── Tax Hooks ───────────────────────────────────────────────────

    def calculate_vat(self, context):
        """Override VAT calculation with Polish 23% rate."""
        invoices = context.get('invoices', [])
        rate = VAT_RATE_PL / 100.0
        vat_collected = 0
        for inv in invoices:
            if inv.customer and inv.customer.tax_type == 'standard':
                vat_collected += inv.amount_base * rate
        return {
            'vat_collected': vat_collected,
            'vat_rate': rate,
            'label': 'VAT',
        }

    def calculate_income_tax(self, context):
        """Override income tax with Polish PIT (flat or progressive)."""
        taxable_income = context.get('taxable_income', 0)
        cfg = self._get_config()

        if cfg.tax_mode == 'flat':
            return self._calc_flat(taxable_income)
        else:
            return self._calc_progressive(taxable_income)

    def _calc_flat(self, taxable_income):
        """Flat tax 19% — no brackets, no tax-free amount."""
        income_tax = 0
        breakdown = []
        if taxable_income > 0:
            tax = taxable_income * FLAT_TAX_RATE
            income_tax = tax
            breakdown.append({
                'bracket': 'All income',
                'rate': 19,
                'amount': taxable_income,
                'tax': tax,
                'active': True,
            })
            # Health insurance (4.9% of income, partially deductible)
            health = taxable_income * HEALTH_RATE_FLAT
            breakdown.append({
                'bracket': 'Health insurance (składka zdrowotna 4.9%)',
                'rate': 4.9,
                'amount': taxable_income,
                'tax': health,
                'active': True,
            })
            income_tax += health

        return {
            'income_tax': income_tax,
            'irpf_breakdown': breakdown,
            'label': 'PIT (flat 19%) + Health',
        }

    def _calc_progressive(self, taxable_income):
        """Progressive tax 12%/32% with 30,000 PLN tax-free allowance."""
        income_tax = 0
        breakdown = []
        prev_limit = 0

        for limit, rate, label in PROGRESSIVE_BRACKETS:
            if taxable_income <= prev_limit:
                break
            amount = min(taxable_income, limit) - prev_limit
            tax = amount * rate
            income_tax += tax
            active = taxable_income <= limit
            breakdown.append({
                'bracket': label,
                'rate': rate * 100,
                'amount': amount,
                'tax': tax,
                'active': active,
            })
            prev_limit = limit

        # Health insurance (9% of income for progressive, non-deductible)
        if taxable_income > 0:
            health = taxable_income * HEALTH_RATE_PROGRESSIVE
            breakdown.append({
                'bracket': 'Health insurance (składka zdrowotna 9%)',
                'rate': 9,
                'amount': taxable_income,
                'tax': health,
                'active': True,
            })
            income_tax += health

        return {
            'income_tax': income_tax,
            'irpf_breakdown': breakdown,
            'label': 'PIT (progressive) + Health',
        }

    # ── Tax Obligations (ZUS contribution) ──────────────────────────

    def get_tax_obligations(self, context):
        """Add ZUS social insurance to tax obligations panel."""
        cfg = self._get_config()
        zus_annual = cfg.zus_monthly * 12

        return {
            'summary_columns': [
                {'label': 'ZUS (annual)', 'value': zus_annual},
            ],
            'breakdown_rows': [
                {'label': f'ZUS Social Insurance ({cfg.zus_monthly:.0f} PLN/mo × 12)',
                 'amount': zus_annual},
            ],
            'notes': [
                f'ZUS: {cfg.zus_monthly:.0f} PLN/month. '
                f'Tax mode: {"flat 19%" if cfg.tax_mode == "flat" else "progressive 12%/32%"}.'
            ],
            'deductions': 0,
            'tax_total': zus_annual,
        }

    def register_routes(self, app):
        pass

    def get_field_labels(self):
        return {
            'nie_number': 'PESEL / NIP',
            'vat_number': 'NIP (VAT)',
            'default_vat_rate': 'VAT / PTU Rate (%)',
            'default_irpf_rate': 'PIT Advance Rate (%)',
        }
