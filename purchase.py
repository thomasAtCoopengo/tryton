"Purchase"

from trytond.osv import fields, OSV
from decimal import Decimal
import datetime
from trytond.netsvc import LocalService
from trytond.report import CompanyReport

_STATES = {
    'readonly': "state != 'draft'",
}


class Purchase(OSV):
    'Purchase'
    _name = 'purchase.purchase'
    _description = __doc__

    company = fields.Many2One('company.company', 'Company', required=True,
            states=_STATES)
    reference = fields.Char('Reference', size=None, readonly=True, select=1)
    description = fields.Char('Description', size=None, states=_STATES)
    state = fields.Selection([
        ('draft', 'Draft'),
        ('quotation', 'Quotation'),
        ('confirmed', 'Confirmed'),
        ('done', 'Done'),
        ('cancel', 'Cancel'),
    ], 'State', readonly=True, required=True)
    purchase_date = fields.Date('Purchase Date', required=True, states=_STATES)
    payment_term = fields.Many2One('account.invoice.payment_term',
        'Payment Term', required=True, states=_STATES)
    party = fields.Many2One('relationship.party', 'Party', change_default=True,
            required=True, states=_STATES, on_change=['party', 'payment_term'])
    contact_address = fields.Many2One('relationship.address', 'Contact Address',
            domain="[('party', '=', party)]", states=_STATES)
    invoice_address = fields.Many2One('relationship.address', 'Invoice Address',
            domain="[('party', '=', party)]", states=_STATES)
    warehouse = fields.Many2One('stock.location', 'Warehouse',
            domain=[('type', '=', 'warehouse')], required=True, states=_STATES)
    currency = fields.Many2One('currency.currency', 'Currency', required=True,
        states=_STATES)
    lines = fields.One2Many('purchase.line', 'purchase', 'Lines',
            states=_STATES)
    comment = fields.Text('Comment')
    #TODO digits must depend of currency
    untaxed_amount = fields.Function('get_untaxed_amount', type='numeric',
            digits=(16, 2), string='Untaxed')
    tax_amount = fields.Function('get_tax_amount', type='numeric',
            digits=(16, 2), string='Tax')
    total_amount = fields.Function('get_total_amount', type='numeric',
            digits=(16, 2), string='Total')
    invoice_method = fields.Selection([
        ('manual', 'Manual'),
        ('order', 'From Order'),
        ('packing', 'From Packing'),
    ], 'Invoice Method', required=True, states=_STATES)
    invoice_state = fields.Selection([
        ('none', 'None'),
        ('waiting', 'Waiting'),
        ('paid', 'Paid'),
        ('exception', 'Exception'),
    ], 'Invoice State', readonly=True, required=True)
    invoices = fields.Many2Many('account.invoice', 'purchase_invoices_rel',
            'purchase', 'invoice', 'Invoices', readonly=True)
    invoices_ignored = fields.Many2Many('account.invoice',
            'purchase_invoice_ignored_rel', 'purchase', 'invoice',
            'Invoices Ignored', readonly=True)
    invoice_paid = fields.Function('get_invoice_paid', type='boolean',
            string='Invoices Paid')
    invoice_exception = fields.Function('get_invoice_exception', type='boolean',
            string='Invoices Exception')
    packing_state = fields.Selection([
        ('none', 'None'),
        ('waiting', 'Waiting'),
        ('received', 'Received'),
        ('exception', 'Exception'),
    ], 'Packing State', readonly=True, required=True)
    packings = fields.Function('get_packings', type='many2many',
            relation='stock.packing.in', string='Packings')
    moves = fields.Function('get_moves', type='many2many',
            relation='stock.move', string='Moves')
    packing_done = fields.Function('get_packing_done', type='boolean',
            string='Packing Done')
    packing_exception = fields.Function('get_packing_exception', type='boolean',
            string='Packings Exception')

    def __init__(self):
        super(Purchase, self).__init__()
        self._rpc_allowed += [
            'button_compute',
        ]

    def default_company(self, cursor, user, context=None):
        company_obj = self.pool.get('company.company')
        if context is None:
            context = {}
        if context.get('company'):
            return company_obj.name_get(cursor, user, context['company'],
                    context=context)[0]
        return False

    def default_state(self, cursor, user, context=None):
        return 'draft'

    def default_purchase_date(self, cursor, user, context=None):
        return datetime.date.today()

    def default_currency(self, cursor, user, context=None):
        company_obj = self.pool.get('company.company')
        currency_obj = self.pool.get('currency.currency')
        if context is None:
            context = {}
        company = None
        if context.get('company'):
            company = company_obj.browse(cursor, user, context['company'],
                    context=context)
            return currency_obj.name_get(cursor, user, company.currency.id,
                    context=context)[0]
        return False

    def default_invoice_method(self, cursor, user, context=None):
        return 'order'

    def default_invoice_state(self, cursor, user, context=None):
        return 'none'

    def default_packing_state(self, cursor, user, context=None):
        return 'none'

    def on_change_party(self, cursor, user, ids, vals, context=None):
        party_obj = self.pool.get('relationship.party')
        address_obj = self.pool.get('relationship.address')
        payment_term_obj = self.pool.get('account.invoice.payment_term')
        res = {
            'invoice_address': False,
            'contact_address': False,
            'payment_term': False,
        }
        if vals.get('party'):
            party = party_obj.browse(cursor, user, vals['party'],
                    context=context)
            res['contact_address'] = party_obj.address_get(cursor, user,
                    party.id, type=None, context=context)
            res['invoice_address'] = party_obj.address_get(cursor, user,
                    party.id, type='invoice', context=context)
            if party.payment_term:
                res['payment_term'] = party.payment_term.id

        if res['contact_address']:
            res['contact_address'] = address_obj.name_get(cursor, user,
                    res['contact_address'], context=context)[0]
        if res['invoice_address']:
            res['invoice_address'] = address_obj.name_get(cursor, user,
                    res['invoice_address'], context=context)[0]
        if res['payment_term']:
            res['payment_term'] = payment_term_obj.name_get(cursor, user,
                    res['payment_term'], context=context)[0]
        return res

    def get_untaxed_amount(self, cursor, user, ids, name, arg, context=None):
        currency_obj = self.pool.get('currency.currency')
        res = {}
        for purchase in self.browse(cursor, user, ids, context=context):
            res.setdefault(purchase.id, Decimal('0.0'))
            for line in purchase.lines:
                if line.type != 'line':
                    continue
                res[purchase.id] += line.amount
            res[purchase.id] = currency_obj.round(cursor, user, purchase.currency,
                    res[purchase.id])
        return res

    def get_tax_context(self, cursor, user, purchase, context=None):
        res = {}
        if purchase.party.lang:
            res['language'] = purchase.party.lang.code
        return res

    def get_tax_amount(self, cursor, user, ids, name, arg, context=None):
        currency_obj = self.pool.get('currency.currency')
        tax_obj = self.pool.get('account.tax')
        res = {}
        for purchase in self.browse(cursor, user, ids, context=context):
            ctx = context.copy()
            ctx.update(self.get_tax_context(cursor, user,
                purchase, context=context))
            res.setdefault(purchase.id, Decimal('0.0'))
            for line in purchase.lines:
                for tax in tax_obj.compute(cursor, user, line.taxes,
                        line.unit_price, line.quantity, context=ctx):
                    res[purchase.id] += tax['amount']
                res[purchase.id] = currency_obj.round(cursor, user, purchase.currency,
                        res[purchase.id])
            res[purchase.id] = currency_obj.round(cursor, user, purchase.currency,
                    res[purchase.id])
        return res

    def get_total_amount(self, cursor, user, ids, name, arg, context=None):
        currency_obj = self.pool.get('currency.currency')
        res = {}
        for purchase in self.browse(cursor, user, ids, context=context):
            res[purchase.id] = currency_obj.round(cursor, user, purchase.currency,
                    purchase.untaxed_amount + purchase.tax_amount)
        return res

    def get_invoice_paid(self, cursor, user, ids, name, args, context=None):
        res = {}
        for purchase in self.browse(cursor, user, ids, context=context):
            val = True
            ignored_ids = [x.id for x in purchase.invoices_ignored]
            for invoice in purchase.invoices:
                if invoice.state != 'paid' \
                        and invoice.id not in ignored_ids:
                    val = False
                    break
            res[purchase.id] = val
        return res

    def get_invoice_exception(self, cursor, user, ids, name, args, context=None):
        res = {}
        for purchase in self.browse(cursor, user, ids, context=context):
            val = False
            ignored_ids = [x.id for x in purchase.invoices_ignored]
            for invoice in purchase.invoices:
                if invoice.state == 'cancel' \
                        and invoice.id not in ignored_ids:
                    val = True
                    break
            res[purchase.id] = val
        return res

    def get_packings(self, cursor, user, ids, name, args, context=None):
        res = {}
        for purchase in self.browse(cursor, user, ids, context=context):
            res[purchase.id] = []
            for line in purchase.lines:
                for move in line.moves:
                    if move.packing_in:
                        if move.packing_in.id not in res[purchase.id]:
                            res[purchase.id].append(move.packing_in.id)
        return res

    def get_moves(self, cursor, user, ids, name, args, context=None):
        res = {}
        for purchase in self.browse(cursor, user, ids, context=context):
            res[purchase.id] = []
            for line in purchase.lines:
                res[purchase.id].extend([x.id for x in line.moves])
        return res

    def get_packing_done(self, cursor, user, ids, name, args, context=None):
        res = {}
        for purchase in self.browse(cursor, user, ids, context=context):
            val = True
            for line in purchase.lines:
                if not line.move_done:
                    val = False
                    break
            res[purchase.id] = val
        return res

    def get_packing_exception(self, cursor, user, ids, name, args,
            context=None):
        res = {}
        for purchase in self.browse(cursor, user, ids, context=context):
            val = False
            ignored_ids = []
            for line in purchase.lines:
                if line.move_exception:
                    val = True
                    break
            res[purchase.id] = val
        return res

    def name_get(self, cursor, user, ids, context=None):
        if not ids:
            return []
        if isinstance(ids, (int, long)):
            ids = [ids]
        res = []
        for purchase in self.browse(cursor, user, ids, context=context):
            res.append((purchase.id, purchase.reference or str(purchase.id) \
                    + ' ' + purchase.party.name))
        return res

    def name_search(self, cursor, user, name='', args=None, operator='ilike',
            context=None, limit=None):
        if args is None:
            args = []
        if name:
            ids = self.search(cursor, user, [('reference', operator, name)] + args,
                    limit=limit, context=context)
        if not ids:
            ids = self.search(cursor, user, [('party', operator, name)] + args,
                    limit=limit, context=context)
        res = self.name_get(cursor, user, ids, context=context)
        return res

    def copy(self, cursor, user, purchase_id, default=None, context=None):
        if default is None:
            default = {}
        default = default.copy()
        default['state'] = 'draft'
        default['reference'] = False
        default['invoice_state'] = 'none'
        default['invoices'] = False
        default['invoices_ignored'] = False
        default['packing_state'] = 'none'
        return super(Purchase, self).copy(cursor, user, purchase_id,
                default=default, context=context)

    def button_compute(self, cursor, user, ids, context=None):
        pass

    def set_reference(self, cursor, user, purchase_id, context=None):
        sequence_obj = self.pool.get('ir.sequence')

        purchase = self.browse(cursor, user, purchase_id, context=context)

        if purchase.reference:
            return True

        reference = sequence_obj.get(cursor, user, 'purchase.purchase')
        self.write(cursor, user, purchase_id, {
            'reference': reference,
            }, context=context)
        return True

    def _get_invoice_line_purchase_line(self, cursor, user, purchase,
            context=None):
        '''
        Return list of invoice line values for each purchase lines
        '''
        line_obj = self.pool.get('purchase.line')
        res = {}
        for line in purchase.lines:
            val = line_obj.get_invoice_line(cursor, user, line,
                    context=context)
            if val:
                res[line.id] = val
        return res

    def create_invoice(self, cursor, user, purchase_id, context=None):
        invoice_obj = self.pool.get('account.invoice')
        journal_obj = self.pool.get('account.journal')
        invoice_line_obj = self.pool.get('account.invoice.line')
        purchase_line_obj = self.pool.get('purchase.line')

        purchase = self.browse(cursor, user, purchase_id, context=context)

        invoice_lines = self._get_invoice_line_purchase_line(cursor, user,
                purchase, context=context)
        if not invoice_lines:
            return

        journal_id = journal_obj.search(cursor, user, [
            ('type', '=', 'expense'),
            ], limit=1, context=context)
        if journal_id:
            journal_id = journal_id[0]

        invoice_id = invoice_obj.create(cursor, user, {
            'company': purchase.company.id,
            'type': 'in_invoice',
            'reference': purchase.reference,
            'journal': journal_id,
            'party': purchase.party.id,
            'contact_address': purchase.contact_address.id,
            'invoice_address': purchase.invoice_address.id,
            'currency': purchase.currency.id,
            'account': purchase.party.account_payable.id,
            'payment_term': purchase.payment_term.id,
        }, context=context)

        for line_id in invoice_lines:
            vals = invoice_lines[line_id]
            vals['invoice'] = invoice_id
            invoice_line_id = invoice_line_obj.create(cursor, user, vals,
                    context=context)
            purchase_line_obj.write(cursor, user, line_id, {
                'invoice_lines': [('add', invoice_line_id)],
                }, context=context)

        invoice_obj.button_compute(cursor, user, [invoice_id], context=context)

        self.write(cursor, user, purchase_id, {
            'invoices': [('add', invoice_id)],
        }, context=context)
        return invoice_id

    def ignore_invoice_exception(self, cursor, user, purchase_id, context=None):
        purchase = self.browse(cursor, user, purchase_id, context=context)
        invoice_ids = []
        for invoice in purchase.invoices:
            if invoice.state == 'cancel':
                invoice_ids.append(invoice.id)
        if invoice_ids:
            self.write(cursor, user, purchase_id, {
                'invoices_ignored': [('add', x) for x in invoice_ids],
            }, context=context)

    def create_move(self, cursor, user, purchase_id, context=None):
        '''
        Create move for each purchase lines
        '''
        line_obj = self.pool.get('purchase.line')

        purchase = self.browse(cursor, user, purchase_id, context=context)
        for line in purchase.lines:
            line_obj.create_move(cursor, user, line, context=context)

    def ignore_packing_exception(self, cursor, user, purchase_id, context=None):
        line_obj = self.pool.get('purchase.line')

        purchase = self.browse(cursor, user, purchase_id, context=context)
        for line in purchase.lines:
            line_obj.ignore_move_exception(cursor, user, line, context=context)

Purchase()


class PurchaseLine(OSV):
    'Purchase Line'
    _name = 'purchase.line'
    _rec_name = 'description'
    _description = __doc__

    purchase = fields.Many2One('purchase.purchase', 'Purchase', ondelete='CASCADE',
            select=1)
    sequence = fields.Integer('Sequence')
    type = fields.Selection([
        ('line', 'Line'),
        ('subtotal', 'Subtotal'),
        ('title', 'Title'),
        ], 'Type', select=1, required=True)
    quantity = fields.Float('Quantity',
            states={
                'invisible': "type != 'line'",
                'required': "type == 'line'",
            })
    unit = fields.Many2One('product.uom', 'Unit',
            states={
                'required': "product",
                'invisible': "type != 'line'",
            }, domain="[('category', '=', (product, 'product'))]")
    product = fields.Many2One('product.product', 'Product',
            states={
                'invisible': "type != 'line'",
            }, on_change=['product', 'unit', 'quantity', 'description',
                'parent.party', 'parent.currency'])
    unit_price = fields.Numeric('Unit Price', digits=(16, 4),
            states={
                'invisible': "type != 'line'",
                'required': "type == 'line'",
            })
    amount = fields.Function('get_amount', type='numeric', string='Amount',
            states={
                'invisible': "type not in ('line', 'subtotal')",
            })
    description = fields.Char('Description', size=None, required=True)
    comment = fields.Text('Comment',
            states={
                'invisible': "type != 'line'",
            })
    taxes = fields.Many2Many('account.tax', 'purchase_line_account_tax',
            'line', 'tax', 'Taxes', domain=[('parent', '=', False)],
            states={
                'invisible': "type != 'line'",
            })
    invoice_lines = fields.Many2Many('account.invoice.line',
            'purchase_line_invoice_lines_rel', 'purchase_line', 'invoice_line',
            'Invoice Lines', readonly=True)
    moves = fields.One2Many('stock.move', 'purchase_line', 'Moves',
            readonly=True)
    moves_ignored = fields.Many2Many('stock.move', 'purchase_line_moves_ignored_rel',
            'purchase_line', 'move', 'Moves Ignored', readonly=True)
    move_done = fields.Function('get_move_done', type='boolean',
            string='Moves Done')
    move_exception = fields.Function('get_move_exception', type='boolean',
            string='Moves Exception')

    def __init__(self):
        super(PurchaseLine, self).__init__()
        self._order.insert(0, ('sequence', 'ASC'))

    def default_type(self, cursor, user, context=None):
        return 'line'

    def default_quantity(self, cursor, user, context=None):
        return 0.0

    def default_unit_price(self, cursor, user, context=None):
        return Decimal('0.0')

    def get_move_done(self, cursor, user, ids, name, args, context=None):
        uom_obj = self.pool.get('product.uom')
        res = {}
        for line in self.browse(cursor, user, ids, context=context):
            val = True
            if line.product.type == 'service':
                res[line.id] = True
                continue
            ignored_ids = [x.id for x in line.moves_ignored]
            quantity = line.quantity
            for move in line.moves:
                if move.state != 'done' \
                        and move.id not in ignored_ids:
                    val = False
                    break
                quantity -= uom_obj.compute_qty(cursor, user, move.uom,
                     move.quantity, line.unit)
            if val:
                if quantity > 0.0:
                    val = False
            res[line.id] = val
        return res

    def get_move_exception(self, cursor, user, ids, name, args, context=None):
        res = {}
        for line in self.browse(cursor, user, ids, context=context):
            val = False
            ignored_ids = [x.id for x in line.moves_ignored]
            for move in line.moves:
                if move.state == 'cancel' \
                        and move.id not in ignored_ids:
                    val = True
                    break
            res[line.id] = val
        return res

    def on_change_product(self, cursor, user, ids, vals, context=None):
        party_obj = self.pool.get('relationship.party')
        product_obj = self.pool.get('product.product')
        uom_obj = self.pool.get('product.uom')
        if context is None:
            context = {}
        if not vals.get('product'):
            return {}
        res = {}

        ctx = context.copy()
        party = None
        if vals.get('parent.party'):
            party = party_obj.browse(cursor, user, vals['parent.party'],
                    context=context)
            if party.lang:
                ctx['language'] = party.lang.code

        product = product_obj.browse(cursor, user, vals['product'],
                context=context)

        ctx2 = context.copy()
        if vals.get('parent.currency'):
            ctx2['currency'] = vals['parent.currency']
        if vals.get('parent.party'):
            ctx2['supplier'] = vals['parent.party']
        if vals.get('unit'):
            ctx2['uom'] = vals['unit']
        res['unit_price'] = product_obj.get_purchase_price(cursor, user,
                [product.id], vals.get('quantity', 0), context=ctx2)[product.id]
        res['taxes'] = []
        for tax in product.supplier_taxes:
            if party:
                if party.get(tax.group.code):
                    res['taxes'].append(party.get(tax.group.code).id)
                    continue
            res['taxes'].append(tax.id)

        if not vals.get('description'):
            res['description'] = product_obj.name_get(cursor, user, product.id,
                    context=ctx)[0][1]

        category = product.default_uom.category
        if not vals.get('unit') \
                or vals.get('unit') not in [x.id for x in category.uoms]:
            res['unit'] = uom_obj.name_get(cursor, user, product.default_uom.id,
                    context=context)[0]
        return res

    def get_amount(self, cursor, user, ids, name, arg, context=None):
        currency_obj = self.pool.get('currency.currency')
        res = {}
        for line in self.browse(cursor, user, ids, context=context):
            if line.type == 'line':
                res[line.id] = currency_obj.round(cursor, user,
                        line.purchase.currency,
                        Decimal(str(line.quantity)) * line.unit_price)
            elif line.type == 'subtotal':
                res[line.id] = Decimal('0.0')
                for line2 in line.purchase.lines:
                    if line2.type == 'line':
                        res[line.id] += currency_obj.round(cursor, user,
                                line2.purchase.currency,
                                Decimal(str(line2.quantity)) * line2.unit_price)
                    elif line2.type == 'subtotal':
                        if line.id == line2.id:
                            break
                        res[line.id] = Decimal('0.0')
            else:
                res[line.id] = Decimal('0.0')
        return res

    def get_invoice_line(self, cursor, user, line, context=None):
        '''
        Return invoice line values for purchase line
        '''
        uom_obj = self.pool.get('product.uom')

        res = {}
        res['sequence'] = line.sequence
        res['type'] = line.type
        res['description'] = line.description
        if line.type != 'line':
            return res
        if line.purchase.invoice_method == 'order':
            res['quantity'] = line.quantity
        else:
            quantity = 0.0
            for move in line.moves:
                if move.state == 'done':
                    quantity += uom_obj.compute_qty(cursor, user, move.uom,
                            move.quantity, line.unit)
            for invoice_line in line.invoice_lines:
                quantity -= uom_obj.compute_qty(cursor, user,
                        invoice_line.unit, invoice_line.quantity, line.unit)
            res['quantity'] = quantity
        if res['quantity'] <= 0.0:
            return None
        res['unit'] = line.unit.id
        res['product'] = line.product.id
        res['unit_price'] = line.unit_price
        res['taxes'] = [('set', [x.id for x in line.taxes])]
        res['account'] = line.product.account_expense_used.id
        return res

    def copy(self, cursor, user, line_id, default=None, context=None):
        if default is None:
            default = {}
        default = default.copy()
        default['moves'] = False
        default['moves_ignored'] = False
        default['invoice_lines'] = False
        return super(PurchaseLine, self).copy(cursor, user, line_id,
                default=default, context=context)

    def create_move(self, cursor, user, line, context=None):
        '''
        Create move line
        '''
        move_obj = self.pool.get('stock.move')
        uom_obj = self.pool.get('product.uom')

        vals = {}
        if line.type != 'line':
            return
        if line.product.type == 'service':
            return
        quantity = line.quantity
        for move in line.moves:
            quantity -= uom_obj.compute_qty(cursor, user, move.uom,
                    move.quantity, line.unit)
        if quantity <= 0.0:
            return
        vals['quantity'] = quantity
        vals['uom'] = line.unit
        vals['product'] = line.product.id
        vals['from_location'] = line.purchase.party.supplier_location.id
        vals['to_location'] = line.purchase.warehouse.input_location.id
        vals['state'] = 'waiting'
        vals['company'] = line.purchase.company.id
        vals['unit_price'] = line.unit_price
        vals['currency'] = line.purchase.currency.id

        move_id = move_obj.create(cursor, user, vals, context=context)

        self.write(cursor, user, line.id, {
            'moves': [('add', move_id)],
        }, context=context)
        return move_id

    def ignore_move_exception(self, cursor, user, line, context=None):
        move_ids = []
        for move in line.moves:
            if move.state == 'cancel':
                move_ids.append(move.id)
        if move_ids:
            self.write(cursor, user, line.id, {
                'moves_ignored': [('add', x) for x in move_ids],
            }, context=context)

PurchaseLine()


class PurchaseReport(CompanyReport):
    _name = 'purchase.purchase'

PurchaseReport()


class Template(OSV):
    _name = "product.template"

    purchasable = fields.Boolean('Purchasable')
    product_suppliers = fields.One2Many('purchase.product_supplier',
            'product', 'Suppliers', states={
                'invisible': "not purchasable",
            })

    def default_purchasable(self, cursor, user, context=None):
        return True

Template()


class Product(OSV):
    _name = 'product.product'

    def get_purchase_price(self, cursor, user, ids, quantity=0, context=None):
        '''
        Return price for product ids.
        context can have :
            - uom
            - supplier
            - currency
        '''
        uom_obj = self.pool.get('product.uom')
        user_obj = self.pool.get('res.user')
        currency_obj = self.pool.get('currency.currency')
        res = {}
        for product in self.browse(cursor, user, ids, context=context):
            res[product.id] = product.cost_price
            if context.get('supplier') and product.product_suppliers:
                supplier_id = context['supplier']
                for product_supplier in product.product_suppliers:
                    if product_supplier.party.id == supplier_id:
                        for price in product_supplier.prices:
                            if price.quantity <= quantity:
                                res[product.id] = price.price
                        break
            if context.get('uom'):
                uom = uom_obj.browse(cursor, user, context['uom'],
                        context=context)
                res[product.id] = uom_obj.compute_price(cursor,
                        user, product.default_uom, res[product.id],
                        uom)
            if context.get('currency'):
                currency = currency_obj.browse(cursor, user,
                        context['currency'], context=context)
                user2 = user_obj.browse(cursor, user, user, context=context)
                if user2.company.currency.id != currency.id:
                    res[product.id] = currency_obj.compute(cursor, user,
                            user2.company.currency, res[product.id],
                            currency, context=context)
        return res

Product()


class ProductSupplier(OSV):
    'Product Supplier'
    _name = 'purchase.product_supplier'
    _description = __doc__

    product = fields.Many2One('product.template', 'Product', required=True,
            ondelete='CASCADE', select=1)
    party = fields.Many2One('relationship.party', 'Supplier', required=True,
            ondelete='CASCADE', select=1)
    name = fields.Char('Name', size=None, translate=True, select=1)
    code = fields.Char('Code', size=None, select=1)
    sequence = fields.Integer('Sequence')
    prices = fields.One2Many('purchase.product_supplier.price',
            'product_supplier', 'Prices')
    company = fields.Many2One('company.company', 'Company', required=True,
            ondelete='CASCADE', select=1)

    def __init__(self):
        super(ProductSupplier, self).__init__()
        self._order.insert(0, ('sequence', 'ASC'))

    def default_company(self, cursor, user, context=None):
        company_obj = self.pool.get('company.company')
        if context is None:
            context = {}
        if context.get('company'):
            return company_obj.name_get(cursor, user, context['company'],
                    context=context)[0]
        return False

ProductSupplier()


class ProductSupplierPrice(OSV):
    'Product Supplier Price'
    _name = 'purchase.product_supplier.price'

    product_supplier = fields.Many2One('purchase.product_supplier',
            'Supplier', required=True, ondelete='CASCADE')
    quantity = fields.Float('Quantity', required=True, help='Minimal quantity')
    price = fields.Numeric('Price', required=True, digits=(16, 4))

    def __init__(self):
        super(ProductSupplierPrice, self).__init__()
        self._order.insert(0, ('quantity', 'ASC'))

    def default_currency(self, cursor, user, context=None):
        company_obj = self.pool.get('company.company')
        currency_obj = self.pool.get('currency.currency')
        if context is None:
            context = {}
        company = None
        if context.get('company'):
            company = company_obj.browse(cursor, user, context['company'],
                    context=context)
            return currency_obj.name_get(cursor, user, company.currency.id,
                    context=context)[0]
        return False

ProductSupplierPrice()


class PackingIn(OSV):
    _name = 'stock.packing.in'

    def write(self, cursor, user, ids, vals, context=None):
        workflow_service = LocalService('workflow')
        purchase_line_obj = self.pool.get('purchase.line')

        res = super(PackingIn, self).write(cursor, user, ids, vals,
                context=context)

        if 'state' in vals and vals['state'] in ('received', 'cancel'):
            purchase_ids = []
            move_ids = []
            if isinstance(ids, (int, long)):
                ids = [ids]
            for packing in self.browse(cursor, user, ids, context=context):
                move_ids.extend([x.id for x in packing.incoming_moves])

            purchase_line_ids = purchase_line_obj.search(cursor, user, [
                ('moves', 'in', move_ids),
                ], context=context)
            if purchase_line_ids:
                for purchase_line in purchase_line_obj.browse(cursor, user,
                        purchase_line_ids, context=context):
                    if purchase_line.purchase.id not in purchase_ids:
                        purchase_ids.append(purchase_line.purchase.id)

            for purchase_id in purchase_ids:
                workflow_service.trg_validate(user, 'purchase.purchase',
                        purchase_id, 'packing_update', cursor)
        return res

PackingIn()


class Move(OSV):
    _name = 'stock.move'

    purchase_line = fields.Many2One('purchase.line', select=1,
            states={
                'readonly': "state != 'draft'",
            })
    purchase = fields.Function('get_purchase', type='many2one',
            relation='purchase.purchase', string='Purchase',
            fnct_search='search_purchase', select=1)

    def get_purchase(self, cursor, user, ids, name, arg, context=None):
        purchase_obj = self.pool.get('purchase.purchase')

        res = {}
        for move in self.browse(cursor, user, ids, context=context):
            res[move.id] = False
            if move.purchase_line:
                res[move.id] = move.purchase_line.purchase.id

        purchase_names = {}
        for purchase_id, purchase_name in purchase_obj.name_get(cursor,
                user, [x for x in res.values() if x], context=context):
            purchase_names[purchase_id] = purchase_name

        for i in res.keys():
            if res[i] and res[i] in purchase_names:
                res[i] = (res[i], purchase_names[res[i]])
            else:
                res[i] = False
        return res

    def search_purchase(self, cursor, user, name, args, context=None):
        args2 = []
        i = 0
        while i < len(args):
            field = args[i][0]
            args2.append(('purchase_line.' + field, args[i][1], args[i][2]))
            i += 1
        return args2

    def write(self, cursor, user, ids, vals, context=None):
        workflow_service = LocalService('workflow')
        purchase_line_obj = self.pool.get('purchase.line')

        res = super(Move, self).write(cursor, user, ids, vals,
                context=context)
        if 'state' in vals and vals['state'] in ('cancel',):
            purchase_ids = []
            purchase_line_ids = purchase_line_obj.search(cursor, user, [
                ('moves', 'in', ids),
                ], context=context)
            if purchase_line_ids:
                for purchase_line in purchase_line_obj.browse(cursor, user,
                        purchase_line_ids, context=context):
                    if purchase_line.purchase.id not in purchase_ids:
                        purchase_ids.append(purchase_line.purchase.id)
            for purchase_id in purchase_ids:
                workflow_service.trg_validate(user, 'purchase.purchase',
                        purchase_id, 'packing_update', cursor)
        return res

Move()
