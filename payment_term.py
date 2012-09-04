#This file is part of Tryton.  The COPYRIGHT file at the top level of
#this repository contains the full copyright notices and license terms.
from trytond.model import ModelView, ModelSQL


class PaymentTerm(ModelSQL, ModelView):
    _name = 'account.invoice.payment_term'
    _history = True

PaymentTerm()


class PaymentTermLine(ModelSQL, ModelView):
    _name = 'account.invoice.payment_term.line'
    _history = True

PaymentTermLine()
