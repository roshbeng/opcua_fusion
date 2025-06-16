# cloud_managements/views.py

from django.shortcuts import render, redirect
import os
from firebase_admin import firestore, storage
from datetime import datetime, timezone
from django.contrib.auth.hashers import make_password
from uuid import uuid4


def index(request):
    return render(request, "cloud_managements/index.html")


def user_accounts(request):
    db = firestore.client()
    users_ref = db.collection("users")
    users = users_ref.stream()

    user_list = []
    for user in users:
        user_data = user.to_dict()
        user_info = {
            "id": user.id,
            "name": user_data.get("name"),
            "email": user_data.get("email"),
            "company_name": user_data.get("company_name"),
            "approved": user_data.get("approved"),
        }
        user_list.append(user_info)

    context = {"users": user_list}
    return render(request, "cloud_managements/user_accounts.html", context)


def user_account_detail(request, id):
    db = firestore.client()
    user_ref = db.collection("users").document(id)
    user_doc = user_ref.get()
    if not user_doc.exists:
        return redirect("user_accounts")

    user_data = user_doc.to_dict()

    if request.method == "POST":

        update_field = request.POST.get("update_field")

        if update_field == "name":
            new_value = request.POST.get("name")
            if new_value:
                user_ref.update({"name": new_value})

        elif update_field == "email":
            new_value = request.POST.get("email")
            if new_value:
                user_ref.update({"email": new_value})

        elif update_field == "job_role":
            new_value = request.POST.get("job_role")
            if new_value:
                user_ref.update({"job_role": new_value})

        elif update_field == "company_name":
            new_value = request.POST.get("company_name")
            if new_value:
                user_ref.update({"company_name": new_value})

        elif update_field == "description":
            new_value = request.POST.get("description")
            if new_value:
                user_ref.update({"description": new_value})

        elif update_field == "approved":
            approved_value = "approved" in request.POST
            user_ref.update({"approved": approved_value})

        elif update_field == "password":
            new_password = request.POST.get("password")
            if new_password:

                hashed_password = make_password(new_password)
                user_ref.update({"password": hashed_password})

        user_ref.update({"account_updated_timestamp": datetime.now(timezone.utc)})

        user_doc = user_ref.get()
        user_data = user_doc.to_dict()

    context = {
        "id": id,
        "user": user_data,
    }
    return render(request, "cloud_managements/user_account_detail.html", context)


def quote_requests(request):
    db = firestore.client()
    quotes_ref = db.collection("quote_requests")
    quotes = quotes_ref.stream()

    quote_list = []
    for quote in quotes:
        quote_data = quote.to_dict()
        user_id = quote_data.get("user_id")
        user_email = None
        user_name = None
        if user_id:
            user_ref = db.collection("users").document(user_id)
            user_doc = user_ref.get()
            if user_doc.exists:
                user_info = user_doc.to_dict()
                user_email = user_info.get("email")
                user_name = user_info.get("name")

        quote_info = {
            "id": quote.id,
            "user_id": user_id,
            "user_email": user_email,
            "user_name": user_name.title() if user_name else None,
            "title": quote_data.get("title").title(),
            "gcode": quote_data.get("gcode"),
            "description": quote_data.get("description").capitalize(),
            "status": quote_data.get("status"),
            "request_timestamp": quote_data.get("request_timestamp"),
            "start_timestamp": quote_data.get("start_timestamp"),
            "finish_timestamp": quote_data.get("finish_timestamp"),
            "updated_timestamp": quote_data.get("updated_timestamp"),
            "file_url": quote_data.get("file_url"),
            "technical_drawing_url": quote_data.get("technical_drawing_url"),
            "quantity": quote_data.get("quantity"),
            "design_units": quote_data.get("design_units"),
            "material": quote_data.get("material"),
            "aluminum_type": quote_data.get("aluminum_type"),
            "surface_finish": quote_data.get("surface_finish"),
            "tolerance": quote_data.get("tolerance"),
            "report": quote_data.get("report"),
        }
        quote_list.append(quote_info)

    context = {"quotes": quote_list}
    return render(request, "cloud_managements/quote_requests.html", context)


def quote_request_detail(request, id):
    db = firestore.client()
    quote_ref = db.collection("quote_requests").document(id)
    quote_doc = quote_ref.get()
    if not quote_doc.exists:
        return redirect("quote_requests")

    quote_data = quote_doc.to_dict()

    if request.method == "POST":
        update_field = request.POST.get("update_field")

        if update_field == "title":
            new_value = request.POST.get("title")
            if new_value:
                quote_ref.update({"title": new_value.title()})

        elif update_field == "description":
            new_value = request.POST.get("description")
            if new_value:
                quote_ref.update({"description": new_value.capitalize()})

        elif update_field == "gcode":
            new_value = request.POST.get("gcode")
            if new_value:
                quote_ref.update({"gcode": new_value})

        elif update_field == "status":
            new_value = request.POST.get("status")
            if new_value:
                quote_ref.update({"status": new_value})

        elif update_field == "start_timestamp":
            new_value = request.POST.get("start_timestamp")
            if new_value:
                try:

                    new_datetime = datetime.strptime(new_value, "%Y-%m-%dT%H:%M")
                    new_datetime = new_datetime.replace(tzinfo=timezone.utc)
                    quote_ref.update({"start_timestamp": new_datetime})
                except ValueError:
                    pass

        elif update_field == "finish_timestamp":
            new_value = request.POST.get("finish_timestamp")
            if new_value:
                try:

                    new_datetime = datetime.strptime(new_value, "%Y-%m-%dT%H:%M")
                    new_datetime = new_datetime.replace(tzinfo=timezone.utc)
                    quote_ref.update({"finish_timestamp": new_datetime})
                except ValueError:
                    pass

        elif update_field == "report":

            if "report_file" in request.FILES:
                report_file = request.FILES["report_file"]

                allowed_extensions = [".zip", ".pdf"]
                _, file_extension = os.path.splitext(report_file.name)
                if file_extension.lower() in allowed_extensions:
                    filename = f"{uuid4()}_{report_file.name}"
                    bucket = storage.bucket()
                    blob = bucket.blob(f"opcua_cloud/{filename}")
                    blob.upload_from_file(report_file)
                    blob.make_public()
                    report_url = blob.public_url
                    quote_ref.update({"report": report_url})
                else:
                    pass

        elif update_field == "file_url":

            if "file_url_file" in request.FILES:
                file_url_file = request.FILES["file_url_file"]
                filename_with_id = f"{uuid4()}_{file_url_file.name}"
                bucket = storage.bucket()
                blob = bucket.blob(f"opcua_cloud/{filename_with_id}")
                blob.upload_from_file(file_url_file)
                blob.make_public()
                file_url = blob.public_url
                quote_ref.update({"file_url": file_url})

        elif update_field == "quantity":
            new_value = request.POST.get("quantity")
            if new_value:
                quote_ref.update({"quantity": new_value})

        elif update_field == "design_units":
            new_value = request.POST.get("design_units")
            if new_value:
                quote_ref.update({"design_units": new_value})

        elif update_field == "material":
            new_value = request.POST.get("material")
            if new_value:
                quote_ref.update({"material": new_value})

        elif update_field == "aluminum_type":
            new_value = request.POST.get("aluminum_type")
            if new_value:
                quote_ref.update({"aluminum_type": new_value})

        elif update_field == "surface_finish":
            new_value = request.POST.get("surface_finish")
            if new_value:
                quote_ref.update({"surface_finish": new_value})

        elif update_field == "tolerance":
            new_value = request.POST.get("tolerance")
            if new_value:
                quote_ref.update({"tolerance": new_value})

        elif update_field == "technical_drawing_url":

            if "technical_drawing_file" in request.FILES:
                technical_drawing_file = request.FILES["technical_drawing_file"]
                filename_with_id = f"{uuid4()}_{technical_drawing_file.name}"
                bucket = storage.bucket()
                blob = bucket.blob(f"opcua_cloud/{filename_with_id}")
                blob.upload_from_file(technical_drawing_file)
                blob.make_public()
                technical_drawing_url = blob.public_url
                quote_ref.update({"technical_drawing_url": technical_drawing_url})

        quote_ref.update({"updated_timestamp": datetime.now(timezone.utc)})

        quote_doc = quote_ref.get()
        quote_data = quote_doc.to_dict()

    context = {
        "id": id,
        "quote": quote_data,
    }
    return render(request, "cloud_managements/quote_request_detail.html", context)
