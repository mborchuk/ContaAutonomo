#!/usr/bin/env python3
"""Database Repositories - Contains all database access logic organized by entity"""

from datetime import datetime
from werkzeug.utils import secure_filename
import os


class BaseRepository:
    """Base repository with common database operations"""

    def __init__(self, db, model):
        self.db = db
        self.model = model

    def get_all(self):
        return self.model.query.all()

    def get_by_id(self, id):
        return self.model.query.get_or_404(id)

    def create(self, **kwargs):
        instance = self.model(**kwargs)
        self.db.session.add(instance)
        self.db.session.commit()
        return instance

    def update(self, instance):
        self.db.session.commit()
        return instance

    def delete(self, instance):
        self.db.session.delete(instance)
        self.db.session.commit()


class ExpenseRepository(BaseRepository):
    """Repository for Expense operations"""

    def __init__(self, db, expense_model, contractor_model):
        super().__init__(db, expense_model)
        self.contractor_model = contractor_model

    def get_paginated(self, page=1, per_page=20, contractor_id=None, category=None,
                     date_from=None, date_to=None):
        query = self.model.query

        if contractor_id:
            query = query.filter_by(contractor_id=contractor_id)
        if category:
            query = query.filter_by(category=category)
        if date_from:
            query = query.filter(self.model.expense_date >= date_from)
        if date_to:
            query = query.filter(self.model.expense_date <= date_to)

        query = query.order_by(self.model.expense_date.desc())
        return query.paginate(page=page, per_page=per_page, error_out=False)

    def get_all_contractors(self):
        return self.contractor_model.query.order_by(self.contractor_model.name).all()

    def get_unique_categories(self):
        categories = self.db.session.query(self.model.category).distinct().filter(self.model.category.isnot(None)).all()
        return [c[0] for c in categories]

    def get_ss_summary_by_year(self):
        """Get Social Security expenses grouped by year with totals"""
        expenses = self.model.query.filter(
            self.model.category == 'Social Security'
        ).order_by(self.model.expense_date.desc()).all()

        summary = {}
        for expense in expenses:
            year = expense.expense_date.year
            if year not in summary:
                summary[year] = {'total': 0.0, 'count': 0, 'expenses': []}
            summary[year]['total'] += expense.amount
            summary[year]['count'] += 1
            summary[year]['expenses'].append(expense)

        return summary

    def get_all_ordered(self):
        """Get all expenses ordered by date descending"""
        return self.model.query.order_by(
            self.model.expense_date.desc()
        ).all()

    def get_grouped_by_year(self, contractor_id=None, category=None, date_from=None, date_to=None):
        """Group expenses by year and quarter"""
        query = self.model.query

        if contractor_id:
            query = query.filter_by(contractor_id=contractor_id)
        if category:
            query = query.filter_by(category=category)
        if date_from:
            query = query.filter(self.model.expense_date >= date_from)
        if date_to:
            query = query.filter(self.model.expense_date <= date_to)

        expenses = query.order_by(self.model.expense_date.desc()).all()

        expenses_by_year = {}
        for expense in expenses:
            year = expense.expense_date.year
            quarter = (expense.expense_date.month - 1) // 3 + 1

            if year not in expenses_by_year:
                expenses_by_year[year] = {}

            if quarter not in expenses_by_year[year]:
                expenses_by_year[year][quarter] = []

            expenses_by_year[year][quarter].append(expense)

        return expenses_by_year

    def get_years_list(self, contractor_id=None, category=None, date_from=None, date_to=None):
        """Get list of years with expenses"""
        expenses_by_year = self.get_grouped_by_year(contractor_id, category, date_from, date_to)
        return sorted(expenses_by_year.keys(), reverse=True) if expenses_by_year else []

    def create_with_file(self, app, file, storage=None, **kwargs):
        file_path = None

        if file and file.filename:
            filename = secure_filename(file.filename)
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"{timestamp}_{filename}"

            if storage:
                file_path = storage.save_file(file, 'expenses_files', filename)
            else:
                upload_folder = os.path.join(app.root_path, 'expenses_files')
                os.makedirs(upload_folder, exist_ok=True)
                file_path = os.path.join('expenses_files', filename)
                file.save(os.path.join(app.root_path, file_path))

        kwargs['file_path'] = file_path
        return self.create(**kwargs)

    def update_with_file(self, app, expense, file, storage=None, **kwargs):
        if file and file.filename:
            filename = secure_filename(file.filename)
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"{timestamp}_{filename}"

            if storage:
                new_path = storage.save_file(file, 'expenses_files', filename)
                if expense.file_path:
                    storage.delete_file(expense.file_path)
            else:
                upload_folder = os.path.join(app.root_path, 'expenses_files')
                os.makedirs(upload_folder, exist_ok=True)
                new_path = os.path.join('expenses_files', filename)
                file.save(os.path.join(app.root_path, new_path))

            expense.file_path = new_path

        for key, value in kwargs.items():
            setattr(expense, key, value)

        return self.update(expense)

    def delete_with_file(self, app, expense, storage=None):
        if expense.file_path:
            if storage:
                storage.delete_file(expense.file_path)
            else:
                file_full_path = os.path.join(app.root_path, expense.file_path)
                if os.path.exists(file_full_path):
                    os.remove(file_full_path)

        self.delete(expense)



class TaxFormRepository(BaseRepository):
    """Repository for TaxForm operations"""

    def get_all_ordered(self):
        return self.model.query.order_by(
            self.model.year.desc(),
            self.model.quarter.desc(),
            self.model.form_type
        ).all()

    def get_grouped_by_year(self):
        forms = self.get_all_ordered()

        forms_by_year = {}
        for form in forms:
            if form.year not in forms_by_year:
                forms_by_year[form.year] = {}

            quarter_key = form.quarter if form.quarter else 0
            if quarter_key not in forms_by_year[form.year]:
                forms_by_year[form.year][quarter_key] = []

            forms_by_year[form.year][quarter_key].append(form)

        return forms_by_year

    def get_years_list(self):
        forms_by_year = self.get_grouped_by_year()
        return sorted(forms_by_year.keys(), reverse=True) if forms_by_year else []

    def find_existing(self, form_type, year, quarter=None):
        return self.model.query.filter_by(
            form_type=form_type,
            year=year,
            quarter=int(quarter) if quarter else None
        ).first()

    def create_with_file(self, file, form_type, year, quarter=None, notes=''):
        base_dir = 'tax_forms'

        if quarter:
            quarter_num = int(quarter)
            upload_dir = os.path.join(base_dir, str(year), f'Q{quarter_num}')
        else:
            upload_dir = os.path.join(base_dir, str(year), 'annual')

        os.makedirs(upload_dir, exist_ok=True)

        file_ext = file.filename.rsplit('.', 1)[1].lower()
        if quarter:
            filename = f"{form_type}-Q{quarter}.{file_ext}"
        else:
            filename = f"{form_type}.{file_ext}"

        file_path = os.path.join(upload_dir, filename)
        file.save(file_path)

        return self.create(
            form_type=form_type,
            year=year,
            quarter=int(quarter) if quarter else None,
            file_path=file_path,
            original_filename=file.filename,
            notes=notes
        )

    def update_with_file(self, tax_form, file, notes=''):
        old_file = tax_form.file_path

        base_dir = 'tax_forms'
        if tax_form.quarter:
            upload_dir = os.path.join(base_dir, str(tax_form.year), f'Q{tax_form.quarter}')
        else:
            upload_dir = os.path.join(base_dir, str(tax_form.year), 'annual')

        os.makedirs(upload_dir, exist_ok=True)

        file_ext = file.filename.rsplit('.', 1)[1].lower()
        if tax_form.quarter:
            filename = f"{tax_form.form_type}-Q{tax_form.quarter}.{file_ext}"
        else:
            filename = f"{tax_form.form_type}.{file_ext}"

        file_path = os.path.join(upload_dir, filename)
        file.save(file_path)

        tax_form.file_path = file_path
        tax_form.original_filename = file.filename
        tax_form.uploaded_at = datetime.utcnow()
        tax_form.notes = notes

        if old_file and old_file != file_path and os.path.exists(old_file):
            os.remove(old_file)

        return self.update(tax_form)

    def delete_with_file(self, tax_form):
        if os.path.exists(tax_form.file_path):
            os.remove(tax_form.file_path)

        self.delete(tax_form)


class InvoiceRepository(BaseRepository):
    """Repository for Invoice operations"""

    def get_by_date_range(self, start_date, end_date, include_cancelled=False):
        query = self.model.query.filter(
            self.model.invoice_date >= start_date,
            self.model.invoice_date <= end_date
        )

        if not include_cancelled:
            query = query.filter(self.model.status != 'cancelled')

        return query.order_by(self.model.invoice_date).all()


class SettingsRepository(BaseRepository):
    """Repository for Settings operations"""

    def get_settings(self):
        return self.model.query.first()

    def get_tracked_currencies(self):
        settings = self.get_settings()
        if settings and settings.tracked_currencies:
            return [c.strip() for c in settings.tracked_currencies.split(',') if c.strip()]
        return ['USD', 'EUR', 'GBP', 'CZK']
