from django import forms
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth import login as auth_login, authenticate, get_user_model
from django.contrib.auth.decorators import login_required
from django.conf import settings
from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from .models import User, Auditorium, Feature, AuditoriumImage, Booking, UserRequest, BookingHistory, Feedback, AdvancePayment
import stripe
import json
import logging
from django.db.models import Sum, Count, F
from django.db.models.functions import TruncMonth
from decimal import Decimal
from django.forms import inlineformset_factory
from django.http import HttpResponseForbidden
from datetime import date, timedelta
from django.utils import timezone

User = get_user_model()
logger = logging.getLogger(__name__)
stripe.api_key = settings.STRIPE_SECRET_KEY

def home(request):
    return render(request, 'index.html')

def login_view(request):
    if request.method == 'POST':
        email = request.POST.get('email')
        password = request.POST.get('password')

        user = authenticate(request, email=email, password=password)
        if user is not None:
            auth_login(request, user)
            if user.role == 'user':
                return redirect('user_index')
            else:
                return redirect('event_host_index')
        else:
            messages.error(request, 'Invalid email or password.')

    return render(request, 'login.html')

def register_user(request):
    context = {}
    if request.method == 'POST':
        email = request.POST.get('email')
        username = request.POST.get('username')
        password = request.POST.get('password')
        role = 'user'  # Default role for user registration

        try:
            user = User.objects.create_user(email=email, username=username, password=password, role=role)
            user.save()
            messages.success(request, 'User registered successfully.')
            return redirect('login')
        except Exception as e:
            context['error'] = str('Email is already registered')

    return render(request, 'register_user.html', context)


def register_auditorium(request):
    context = {}
    if request.method == 'POST':
        username = request.POST.get('username')
        email = request.POST.get('email')
        password1 = request.POST.get('password1')
        password2 = request.POST.get('password2')
        location = request.POST.get('location')
        capacity = request.POST.get('capacity')
        price = request.POST.get('price')
        images = request.FILES.get('images')

        if password1 == password2:
            if not User.objects.filter(email=email).exists():
                try:
                    user = User.objects.create_user(username=username, email=email, password=password1, role='host')
                    user.save()

                    auditorium = Auditorium.objects.create(
                        user=user,
                        location=location,
                        capacity=capacity,
                        price=price,
                        images=images,
                        approved=False
                    )
                    auditorium.save()

                    messages.success(request, 'Auditorium registration request sent for approval.')
                    return redirect('login')
                except Exception as e:
                    messages.error(request, f'An error occurred while creating the auditorium request: {e}')
            else:
                context['error'] = str('Email is already registered')
        else:
            context['error'] = str('Passwords do not match.')

    return render(request, 'register_auditorium.html', context)

@login_required
def event_host_index(request):
    auditorium = Auditorium.objects.get(user=request.user)  # Assuming only one auditorium per user for simplicity

    AuditoriumFeatureFormset = inlineformset_factory(
        Auditorium,
        Feature,
        fields=('name', 'amount'),  # Corrected field names
        extra=1,  # Number of extra forms
    )

    if request.method == 'POST':
        formset = AuditoriumFeatureFormset(request.POST, instance=auditorium)
        if formset.is_valid():
            formset.save()
            return redirect('event_host_index')  # Redirect to the same page after submission
    else:
        formset = AuditoriumFeatureFormset(instance=auditorium)

    context = {
        'formset': formset,
        'auditorium': auditorium,
    }
    return render(request, 'event_host_index.html', context)

@login_required
def event_features(request, auditorium_id):
    auditorium = get_object_or_404(Auditorium, id=auditorium_id)
    features = Feature.objects.filter(auditorium=auditorium)
    images = AuditoriumImage.objects.filter(auditorium=auditorium)

    class FeatureForm(forms.ModelForm):
        class Meta:
            model = Feature
            fields = ['name', 'amount']

    class ImageForm(forms.ModelForm):
        class Meta:
            model = AuditoriumImage
            fields = ['image']

    feature_form = FeatureForm()
    image_form = ImageForm()

    if request.method == 'POST':
        if 'add_feature' in request.POST:
            feature_form = FeatureForm(request.POST)
            if feature_form.is_valid():
                feature = feature_form.save(commit=False)
                feature.auditorium = auditorium
                feature.save()
                return redirect('event_features', auditorium_id=auditorium_id)
        elif 'add_image' in request.POST:
            image_form = ImageForm(request.POST, request.FILES)
            if image_form.is_valid():
                image = image_form.save(commit=False)
                image.auditorium = auditorium
                image.save()
                return redirect('event_features', auditorium_id=auditorium_id)
        elif 'delete_features' in request.POST:
            feature_ids = request.POST.getlist('feature_ids')
            Feature.objects.filter(id__in=feature_ids).delete()
            return redirect('event_features', auditorium_id=auditorium_id)
        elif 'delete_images' in request.POST:
            image_ids = request.POST.getlist('image_ids')
            AuditoriumImage.objects.filter(id__in=image_ids).delete()
            return redirect('event_features', auditorium_id=auditorium_id)

    context = {
        'auditorium': auditorium,
        'features': features,
        'images': images,
        'feature_form': feature_form,
        'image_form': image_form,
    }
    return render(request, 'event_features.html', context)

@login_required
def event_schedules(request, auditorium_id):
    auditorium = get_object_or_404(Auditorium, id=auditorium_id)
    bookings = Booking.objects.filter(auditorium=auditorium)
    booked_dates = {booking.date.strftime('%Y-%m-%d'): True for booking in bookings}
    booked_dates_json = json.dumps(booked_dates)
    return render(request, 'event_schedules.html', {'auditorium': auditorium, 'booked_dates_json': booked_dates_json})

@login_required
def manage_booking(request, auditorium_id):
    auditorium = get_object_or_404(Auditorium, id=auditorium_id)
    data = json.loads(request.body)
    booking_date = data.get('date')
    book = data.get('book', False)

    if not booking_date or not isinstance(booking_date, str):
        return JsonResponse({'status': 'error', 'message': 'Invalid date format'}, status=400)

    try:
        booking_date = date.fromisoformat(booking_date)
    except ValueError:
        return JsonResponse({'status': 'error', 'message': 'Invalid date format'}, status=400)

    if booking_date < date.today():
        return JsonResponse({'status': 'error', 'message': 'Cannot book past dates'}, status=400)

    booking, created = Booking.objects.get_or_create(auditorium=auditorium, user=request.user, date=booking_date)

    if not book:
        booking.delete()
        return JsonResponse({'status': 'cancelled', 'message': f'Booking for {booking_date} has been cancelled'})
    else:
        return JsonResponse({'status': 'booked', 'message': f'Booking for {booking_date} has been confirmed'})
    
@login_required
def user_index(request):
    auditoriums = Auditorium.objects.filter(approved=True)
    context = {
        'auditoriums': auditoriums,
        'user': request.user,
    }
    return render(request, 'user_index.html', context)

@login_required
def user_event_schedules(request, auditorium_id):
    auditorium = get_object_or_404(Auditorium, id=auditorium_id)
    bookings = Booking.objects.filter(auditorium=auditorium)
    
    # Get all booked dates for the auditorium
    booked_dates = {booking.date.strftime('%Y-%m-%d'): True for booking in bookings}

    # Calculate all dates between today and a certain number of days in the future
    start_date = date.today()
    end_date = date.today() + timedelta(days=30)  # Adjust the number of days as needed
    all_dates = [start_date + timedelta(days=i) for i in range((end_date - start_date).days + 1)]

    # Determine vacant dates (dates that are not booked)
    vacant_dates = {}
    for d in all_dates:
        date_str = d.strftime('%Y-%m-%d')
        if date_str not in booked_dates:
            vacant_dates[date_str] = False  # Use False to indicate vacant dates

    vacant_dates_json = json.dumps(vacant_dates)
    return render(request, 'user_event_schedules.html', {'auditorium': auditorium, 'vacant_dates_json': vacant_dates_json})

def user_bookings(request):
    return render(request, 'user_bookings.html')

def auditorium_list(request, auditorium_id):
    auditorium = Auditorium.objects.get(id=auditorium_id)
    features = auditorium.auditorium_features.all()

    audi = get_object_or_404(Auditorium, id=auditorium_id)
    
    # Get all feedbacks related to this auditorium
    feedbacks = Feedback.objects.filter(auditorium=audi)

    context = {
        'auditorium': auditorium,
        'feedbacks': feedbacks,
    }

    return render(request, 'auditorium_list.html', context)

def book_calendar(request, auditorium_id):
    auditorium = get_object_or_404(Auditorium, id=auditorium_id)
    bookings = Booking.objects.filter(auditorium=auditorium)
    booked_dates = {booking.date.strftime('%Y-%m-%d'): True for booking in bookings}
    booked_dates_json = json.dumps(booked_dates)
    return render(request, 'book_calendar.html', {'auditorium': auditorium, 'booked_dates_json': booked_dates_json})

@login_required
def auditorium_details(request, auditorium_id):
    auditorium = get_object_or_404(Auditorium, id=auditorium_id)
    features = auditorium.auditorium_features.all()
    date = request.GET.get('date')  # Fetch the date from URL query parameters

    if request.method == 'POST':
        date = request.POST.get('date')  # Ensure you are capturing the date correctly
        final_price = request.POST.get('final_price')
        selected_features = request.POST.getlist('features')

        if not date:
            messages.error(request, 'Date is required.')
            return redirect('auditorium_details', auditorium_id=auditorium_id)

        booking_request = UserRequest(
            user=request.user,
            auditorium=auditorium,
            date=date,
            final_price=final_price
        )
        booking_request.save()

        for feature_id in selected_features:
            feature = Feature.objects.get(id=feature_id)
            booking_request.features.add(feature)

        booking_request.save()

        messages.success(request, 'Auditorium booking requested successfully!')
        return redirect('user_index')

    context = {
        'auditorium': auditorium,
        'features': features,
        'date': date
    }
    return render(request, 'auditorium_details.html', context)

@login_required
def user_requests(request):
    requests = UserRequest.objects.filter(auditorium__user=request.user, approved=False, rejected=False)
    context = {
        'requests': requests
    }
    return render(request, 'user_requests.html', context)

@login_required
def approve_request(request, request_id):
    user_request = get_object_or_404(UserRequest, id=request_id)

    # Check if the user is the owner of the auditorium
    if request.user != user_request.auditorium.user:
        return HttpResponseForbidden("You do not have permission to approve this request.")

    stripe.api_key = 'sk_test_51PYPLEEowOqVQOI5EO3xfdxlXeZumfYIelTtbrWLCdCsipg9l3E2BmQafQ5hstCRoogt9qXI8CJPGIgRswhNDnVd00alIj4ZC2'
    success_url = f"{request.scheme}://{request.get_host()}/success/"
    cancel_url = f"{request.scheme}://{request.get_host()}/cancel/"

    try:
        session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price_data': {
                    'currency': 'inr',
                    'product_data': {
                        'name': user_request.auditorium.user.username,
                    },
                    'unit_amount': int(user_request.final_price * 100),
                },
                'quantity': 1,
            }],
            mode='payment',
            success_url=success_url,
            cancel_url=cancel_url,
        )
        user_request.stripe_payment_intent_id = session.id
        user_request.approved = True
        user_request.payment_requested = True
        user_request.save()

    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)

    return redirect('user_requests')

@login_required
def reject_request(request, request_id):
    user_request = get_object_or_404(UserRequest, id=request_id)

    # Check if the user is the owner of the auditorium
    if request.user != user_request.auditorium.user:
        return HttpResponseForbidden("You do not have permission to reject this request.")

    user_request.rejected = True
    user_request.save()

    return redirect('user_requests')

@login_required
def user_messages(request):
    payment_requests = UserRequest.objects.filter(user=request.user, approved=True, payment_requested=True)
    context = {
        'payment_requests': payment_requests
    }
    return render(request, 'user_messages.html', context)

@login_required
def payment_form(request, request_id):
    print(f"Request ID: {request_id}")  # Debugging message
    try:
        user_request = UserRequest.objects.get(id=request_id)
        print(f"User Request found: {user_request}")  # Debugging message
    except UserRequest.DoesNotExist:
        print("User Request not found.")  # Debugging message
        messages.error(request, "User Request not found.")
        return redirect('user_messages')
    
    return render(request, 'payment_form.html', {'user_request': user_request})

@login_required
@csrf_exempt
def process_payment(request, request_id):
    if request.method == 'POST':
        name = request.POST.get('name')
        card_number = request.POST.get('card_number')
        cvv = request.POST.get('cvv')

        # Validate card number and CVV
        if len(card_number) != 16 or not card_number.isdigit():
            messages.error(request, "Card number must be exactly 16 digits.")
            return redirect('payment_form', request_id=request_id)

        if len(cvv) != 3 or not cvv.isdigit():
            messages.error(request, "CVV must be exactly 3 digits.")
            return redirect('payment_form', request_id=request_id)

        try:
            # Retrieve the user request record
            user_request = UserRequest.objects.get(id=request_id)

            advance_payment = AdvancePayment.objects.filter(user_request=user_request).first()

            if advance_payment:
                # Deduct the advance payment amount from the final price
                final_price = user_request.final_price - advance_payment.amount_paid
            else:
                final_price = user_request.final_price

            # Create a new entry in BookingHistory
            features_selected = ", ".join([feature.name for feature in user_request.features.all()])
            BookingHistory.objects.create(
                auditorium=user_request.auditorium,
                user=user_request.user,
                date_booked=user_request.date,
                features_selected=features_selected,
                final_price=user_request.final_price,
                card_number=card_number,
                cvv=cvv
            )

            # Create a new entry in Booking
            Booking.objects.create(
                auditorium=user_request.auditorium,
                user=user_request.user,
                date=user_request.date
            )

            # Optionally, delete the original user request entry if needed
            user_request.delete()

            return redirect('success')  # Replace 'success' with your success page name

        except UserRequest.DoesNotExist:
            messages.error(request, "User Request not found.")
            return redirect('user_messages')

    return redirect('user_messages')

@login_required
def cancel_payment(request, request_id):
    user_request = get_object_or_404(UserRequest, id=request_id)
    user_request.delete()
    messages.success(request, 'Payment request cancelled successfully.')
    return redirect('user_messages')

def success_page(request):
    return render(request, 'success.html')

@login_required
def event_my_bookings(request):
    try:
        # Get the auditorium associated with the logged-in user
        auditorium = Auditorium.objects.get(user=request.user)
        
        # Get all bookings for the auditorium
        bookings = BookingHistory.objects.filter(auditorium=auditorium)
        
        # Get bookings grouped by month
        bookings_by_month = BookingHistory.objects.filter(auditorium=auditorium)\
            .annotate(month=TruncMonth('date_booked'))\
            .values('month')\
            .annotate(
                total_bookings=Count('id'),
                total_income=Sum(F('final_price') * Decimal('0.85'))  # Calculate auditorium amount
            )\
            .order_by('-month')

    except Auditorium.DoesNotExist:
        bookings = []
        bookings_by_month = []

    context = {
        'bookings': bookings,
        'bookings_by_month': bookings_by_month,
        'auditorium': auditorium
    }
    return render(request, 'event_my_bookings.html', context)

@login_required
def user_my_bookings(request):
    today = timezone.now().date()
    bookings = BookingHistory.objects.filter(user=request.user)

    for booking in bookings:
        original_price = booking.final_price / Decimal('0.98')
        booking.is_canceled = booking.final_price < original_price
        booking.save()

    context = {
        'bookings': bookings,
        'today': today
    }
    return render(request, 'user_my_bookings.html', context)

@login_required
def view_requests(request):
    user_requests = UserRequest.objects.filter(user=request.user)
    return render(request, 'view_requests.html', {'user_requests': user_requests})

@login_required
def feedback(request, booking_id):
    booking = get_object_or_404(Booking, id=booking_id)

    if request.method == 'POST':
        feedback_text = request.POST.get('feedback_text')
        rating = request.POST.get('rating')

        # Validate the input
        if not feedback_text or not rating:
            messages.error(request, "Please provide both feedback and rating.")
            return render(request, 'feedback.html', {'booking': booking})

        # Save the feedback
        feedback = Feedback.objects.create(
            user=booking.user,
            booking=booking,
            auditorium=booking.auditorium,  # Assuming auditorium is linked to the booking
            feedback_text=feedback_text,
            rating=rating
        )
        messages.success(request, "Thank you for your feedback!")
        return redirect('user_my_bookings')

    return render(request, 'feedback.html', {'booking': booking})
    
def cancel_booking(request, booking_id):
    booking = get_object_or_404(BookingHistory, id=booking_id)
   
    # Calculate refund and auditorium earnings
    refund_amount = booking.final_price * 98 / 100
    auditorium_earnings = booking.final_price * Decimal('0.02')

    # Update the final_price to reflect the refund
    booking.final_price = refund_amount
    booking.is_canceled = True
    booking.admin_amount = Decimal('0.00')
    booking.save()

    # Optional: You can display a message to the user
    messages.success(request, f'Booking canceled. You have been refunded {refund_amount}. The auditorium earns {auditorium_earnings}.')

    return redirect('user_my_bookings')      

@login_required
def process_advance_payment(request, request_id):
    try:
        user_request = get_object_or_404(UserRequest, id=request_id, user=request.user)
        advance_amount = user_request.final_price * Decimal('0.10')  # Calculate 10% advance payment
    except UserRequest.DoesNotExist:
        messages.error(request, "User Request not found.")
        return redirect('user_messages')

    if request.method == 'POST':
        # Assuming you have a form or payment processing logic here
        # You can include Stripe or any payment gateway logic as needed

        # For simplicity, let's assume payment is successful
        user_request.advance_paid = True
        user_request.advance_amount = advance_amount
        user_request.save()

        # Redirect to a success page or back to messages
        messages.success(request, f"Advance payment of {advance_amount} processed successfully.")
        return redirect('user_messages')

    context = {
        'user_request': user_request,
        'advance_amount': advance_amount,
    }
    return render(request, 'advance_payment_form.html', context)

@login_required
@csrf_exempt
def pay_advance(request, request_id):
    user_request = get_object_or_404(UserRequest, id=request_id, user=request.user)
    advance_amount = user_request.final_price * Decimal('0.10')

    if request.method == 'POST':
        card_number = request.POST.get('card_number')
        cvv = request.POST.get('cvv')

        # Validate card number and CVV
        if len(card_number) != 16 or not card_number.isdigit():
            messages.error(request, "Card number must be exactly 16 digits.")
            return render(request, 'advance_payment_form.html', {
                'user_request': user_request,
                'request_id': request_id,
                'advance_amount': advance_amount,
                'error_message': "Card number must be exactly 16 digits."
            })

        if len(cvv) != 3 or not cvv.isdigit():
            messages.error(request, "CVV must be exactly 3 digits.")
            return render(request, 'advance_payment_form.html', {
                'user_request': user_request,
                'request_id': request_id,
                'advance_amount': advance_amount,
                'error_message': "CVV must be exactly 3 digits."
            })

        # Check if an AdvancePayment already exists for this user request
        if AdvancePayment.objects.filter(user_request=user_request).exists():
            messages.error(request, "Advance payment has already been processed for this request.")
            return render(request, 'advance_payment_form.html', {
                'user_request': user_request,
                'request_id': request_id,
                'advance_amount': advance_amount,
                'error_message': "Advance payment has already been processed for this request."
            })

        try:
            # Create the advance payment entry
            AdvancePayment.objects.create(
                user_request=user_request,
                amount_paid=advance_amount,
                card_number=card_number,
                cvv=cvv
            )
            
            # Create the Booking instance
            Booking.objects.create(
                auditorium=user_request.auditorium,
                user=user_request.user,
                date=user_request.date
            )

            # Create the BookingHistory instance
            BookingHistory.objects.create(
                auditorium=user_request.auditorium,
                user=user_request.user,
                date_booked=user_request.date,
                features_selected=', '.join([feature.name for feature in user_request.features.all()]),
                final_price=advance_amount,
                card_number=card_number,
                cvv=cvv,
                admin_amount=user_request.final_price * Decimal('0.15'),
                is_canceled=False
            )
            
            # Delete the UserRequest after successful payment and booking creation
            user_request.delete()

            # Redirect to a success page or back to the user requests page
            messages.success(request, f"Advance payment of {advance_amount} processed successfully. Booking confirmed.")
            return redirect('success')
        except Exception as e:
            # Handle any unexpected errors
            messages.error(request, f"An error occurred: {str(e)}")
            return render(request, 'advance_payment_form.html', {
                'user_request': user_request,
                'request_id': request_id,
                'advance_amount': advance_amount,
                'error_message': f"An error occurred: {str(e)}"
            })

    # Render the payment form if GET request
    return render(request, 'advance_payment_form.html', {
        'user_request': user_request,
        'request_id': request_id,
        'advance_amount': advance_amount
    })


def validate_email(request):
    email = request.GET.get('email', None)
    data = {
        'is_taken': User.objects.filter(email=email).exists()
    }
    return JsonResponse(data)