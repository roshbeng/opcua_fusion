# opcua_aggregation_home/views.py

from django.shortcuts import render, redirect
from django.http import HttpResponseBadRequest
from django.http import JsonResponse
from django.core.cache import cache
from datetime import datetime, timezone
import json, threading, time, uuid, re
from firebase_admin import firestore, storage
from google.cloud.firestore_v1.base_query import FieldFilter
from opcua import Client
from opcua.ua import NodeClass
from opcua_client.views import (
    browse_method_nodes,
    browse_node_names,
    find_parent_node_id,
)

db = firestore.client()


def index(request):
    if request.method == "POST":
        endpoint = request.POST.get("endpoint", "")
        namespaceuri = request.POST.get("namespaceuri", "")
        machine = request.POST.get("machine", "")

    else:
        return render(request, "opcua_aggregation_home/index.html")


def remove_connection(request):
    if request.method == "POST":
        try:
            data = json.loads(request.body)
            endpoint = data.get("endpoint", "").strip()

            if not endpoint:
                return JsonResponse({"message": "Endpoint not provided."}, status=400)

            current_connections_ref = db.collection("current_connections")

            query = current_connections_ref.where("endpoint", "==", endpoint).limit(1)
            docs = list(query.stream())

            if docs:
                doc = docs[0]
                doc.reference.delete()

        except Exception as e:
            print(f"Error removing connection: {e}")
            return JsonResponse(
                {"message": "Error while removing the connection."},
                status=500,
            )


def schedule_machines(request):
    if request.method == "POST":
        process_count = request.POST.get("process_count")

        docs = db.collection("current_connections").stream()
        machine_endpoint_connections = {}
        for doc in docs:
            data = doc.to_dict()
            machine_endpoint_connections[data["endpoint"]] = data

        endpoints_and_machines = [
            {
                "endpoint": conn["endpoint"],
                "machine": conn["machine"],
                "namespaceuri": conn["namespaceuri"],
            }
            for conn in machine_endpoint_connections.values()
        ]

        all_method_data = {}

        for connection in endpoints_and_machines:
            endpoint = connection["endpoint"]
            client = Client(endpoint)
            try:
                client.connect()
                client.load_type_definitions()
                object_node = client.get_objects_node()
                method_data = browse_method_nodes(object_node)
                all_method_data[endpoint] = method_data
            finally:
                client.disconnect()

        if process_count and process_count.isdigit():
            process_count = int(process_count)
            if 1 <= process_count <= 50:
                context = {
                    "endpoints_and_machines": endpoints_and_machines,
                    "all_method_data": all_method_data,
                    "process_count": process_count,
                    "process_range": range(1, process_count + 1),
                }
                print(context)
                return render(
                    request,
                    "opcua_aggregation_home/schedule_machine_process_chain.html",
                    context,
                )

    docs = db.collection("current_connections").stream()
    machine_endpoint_connections = {}
    for doc in docs:
        data = doc.to_dict()
        machine_endpoint_connections[data["endpoint"]] = doc.to_dict()

    context = {"machine_endpoint_connections": machine_endpoint_connections}

    return render(request, "opcua_aggregation_home/schedule_machines.html", context)


def schedule_machine_process_chain(request):

    if request.method == "POST":
        process_method_order = {}

        for key, value in request.POST.items():
            if key.startswith("process"):
                process_number = key.replace("process", "")
                method_name, endpoint, namespaceuri = value.split("|")
                process_method_order[f"process_{process_number}"] = {
                    "method_name": method_name,
                    "endpoint": endpoint,
                    "namespaceuri": namespaceuri,
                }

        unique_current_endpoints = set()

        for process in process_method_order.values():
            unique_current_endpoints.add(process["endpoint"])

        unique_current_endpoints = list(unique_current_endpoints)

        all_method_data = {}

        for endpoint in unique_current_endpoints:
            client = Client(endpoint)
            try:
                client.connect()
                client.load_type_definitions()
                object_node = client.get_objects_node()
                method_data = browse_method_nodes(object_node)
                all_method_data[endpoint] = method_data
            finally:
                client.disconnect()

        method_context_data = {}

        for process_key, process_info in process_method_order.items():
            method_name = process_info["method_name"]
            endpoint = process_info["endpoint"]
            namespaceuri = process_info["namespaceuri"]

            method_data = all_method_data[endpoint][method_name]

            method_context_data[process_key] = {
                "method_name": method_name,
                "endpoint": endpoint,
                "namespaceuri": namespaceuri,
                "description": method_data.get("description", ""),
                "browse_name": method_data.get("browse_name", ""),
                "input_arguments": method_data.get("input_arguments", []),
                "num_arguments": len(method_data.get("input_arguments", [])),
            }

        process_count = len(process_method_order)
        process_numbers = list(range(1, process_count + 1))

        context = {
            "method_context_data": method_context_data,
            "process_count": process_count,
            "process_numbers": process_numbers,
        }

        return render(
            request,
            "opcua_aggregation_home/schedule_machine_process_inputs.html",
            context,
        )

    return JsonResponse({"status": "error", "data": "Error"})


def schedule_machine_process_inputs(request):
    if request.method == "POST":
        process_data = {}

        process_keys = set()
        for key in request.POST.keys():
            if key.endswith("_method_name"):
                process_key = key.replace("_method_name", "")
                process_keys.add(process_key)

        for process_key in process_keys:
            method_name = request.POST.get(f"{process_key}_method_name")
            endpoint = request.POST.get(f"{process_key}_endpoint")
            namespaceuri = request.POST.get(f"{process_key}_namespaceuri")

            input_arguments = []

            num_arguments = int(request.POST.get(f"{process_key}_num_arguments", 0))

            if num_arguments > 0:
                for arg_index in range(1, num_arguments + 1):
                    argument_option = request.POST.get(
                        f"{process_key}_argument_{arg_index}_option"
                    )

                    if argument_option == "include_value":
                        arg_value = request.POST.get(
                            f"{process_key}_argument_{arg_index}_value"
                        )
                        input_arguments.append(arg_value)
                    elif argument_option == "use_custom_input":
                        custom_input = request.POST.get(
                            f"{process_key}_argument_{arg_index}_custom_input"
                        )
                        input_arguments.append(custom_input)
                    else:
                        input_arguments.append(None)
            else:
                call_method = (
                    request.POST.get(f"{process_key}_call_method") == "call_method"
                )
                if call_method:
                    input_arguments = []
                else:
                    continue

            process_data[process_key] = {
                "method_name": method_name,
                "endpoint": endpoint,
                "namespaceuri": namespaceuri,
                "input_arguments": input_arguments,
            }

        scheduled_machine_process_unedited = {
            "scheduled_process": process_data,
            "created_timestamp": datetime.now(timezone.utc),
        }

        for process in process_data.values():
            process["output_argument"] = ""

        scheduled_machine_process_execution = {
            "scheduled_process": process_data,
            "created_timestamp": datetime.now(timezone.utc),
            "start_timestamp": "",
            "stop_timestamp": "",
            "status": "scheduled",
            "errors": None,
            "last_update_timestamp": datetime.now(timezone.utc),
        }

        scheduled_machine_process_unedited_collection_ref = db.collection(
            "scheduled_machine_process_unedited"
        )
        scheduled_machine_process_unedited_collection_ref.add(
            scheduled_machine_process_unedited
        )[1]

        scheduled_machine_process_execution_collection_ref = db.collection(
            "scheduled_machine_process_execution"
        )
        scheduled_machine_process_execution_doc_ref = (
            scheduled_machine_process_execution_collection_ref.add(
                scheduled_machine_process_execution
            )[1]
        )
        scheduled_machine_process_execution_doc_id = (
            scheduled_machine_process_execution_doc_ref.id
        )

        return render(
            request,
            "opcua_aggregation_home/schedule_machine_process_start.html",
            {
                "scheduled_machine_process_execution_doc_id": scheduled_machine_process_execution_doc_id
            },
        )

    return JsonResponse({"status": "error", "data": "Error"})


def schedule_machine_process_start(request):
    if request.method == "GET":
        scheduled_machine_process_execution_doc_id = request.GET.get("doc_id")

        if not scheduled_machine_process_execution_doc_id:
            return JsonResponse(
                {"status": "error", "message": "Document ID is missing."}, status=400
            )

        process_id = str(uuid.uuid4())

        thread = threading.Thread(
            target=schedule_machine_process_execution,
            args=(process_id, scheduled_machine_process_execution_doc_id),
        )
        thread.start()

        return render(
            request,
            "opcua_aggregation_home/schedule_machine_process_execution.html",
            {"process_id": process_id},
        )


def schedule_machine_process_execution(
    process_id, scheduled_machine_process_execution_doc_id
):

    doc_ref = db.collection("scheduled_machine_process_execution").document(
        scheduled_machine_process_execution_doc_id
    )
    doc = doc_ref.get()
    if doc.exists:
        scheduled_machine_process_execution = doc.to_dict()
    else:
        progress_message = f"Process Terminated: Scheduled Process id does'nt exist. All Scheduled Processes Terminated"
        progress_messages = cache.get(f"progress_{process_id}", [])
        progress_messages.append(progress_message)
        cache.set(f"progress_{process_id}", progress_messages)
        return

    scheduled_machine_process_data = scheduled_machine_process_execution[
        "scheduled_process"
    ]

    doc_ref.update(
        {
            "status": "progress",
            "start_timestamp": datetime.now(timezone.utc),
            "last_update_timestamp": datetime.now(timezone.utc),
        }
    )

    def extract_process_number(name):
        return int(name.split("_")[1])

    process_names = sorted(
        scheduled_machine_process_data.keys(), key=extract_process_number
    )

    step = 0

    for process_name in process_names:
        step += 1
        data = scheduled_machine_process_data[process_name]
        endpoint = data["endpoint"]
        method_name = data["method_name"]
        input_arguments = data["input_arguments"]
        namespaceuri = data["namespaceuri"]

        collection_ref = db.collection("current_connections")
        field_filter1 = FieldFilter("endpoint", "==", endpoint)
        field_filter2 = FieldFilter("namespaceuri", "==", namespaceuri)

        query = (
            collection_ref.where(filter=field_filter1)
            .where(filter=field_filter2)
            .limit(1)
        )
        results = query.stream()

        if not any(results):
            doc_ref.update(
                {
                    "error": f"{endpoint} not connected",
                    "last_update_timestamp": datetime.now(timezone.utc),
                }
            )
            progress_message = f"Process Terminated: {endpoint} not connected; failed on Process {step}. All Scheduled Processes Terminated"
            progress_messages = cache.get(f"progress_{process_id}", [])
            progress_messages.append(progress_message)
            cache.set(f"progress_{process_id}", progress_messages)
            return
        else:
            try:
                client = Client(endpoint, timeout=480)
                client.connect()
                object_node = client.get_objects_node()
                node_names_and_ids = browse_node_names(object_node)
                method_data = browse_method_nodes(object_node)
            except Exception as e:
                doc_ref.update(
                    {
                        "error": f"opcua client connection to {endpoint} under failed",
                        "last_update_timestamp": datetime.now(timezone.utc),
                    }
                )
                progress_message = f"Process Terminated: Connection to {endpoint} under failed on Process {step}. All Scheduled Processes Terminated"
                progress_messages = cache.get(f"progress_{process_id}", [])
                progress_messages.append(progress_message)
                cache.set(f"progress_{process_id}", progress_messages)
                return

            progress_message = f"Progress: Process {step}, method: {method_name} started on {endpoint} under namespaceuri {namespaceuri}"
            progress_messages = cache.get(f"progress_{process_id}", [])
            progress_messages.append(progress_message)
            cache.set(f"progress_{process_id}", progress_messages)

            input_data_types = [
                each["datatype"] for each in method_data[method_name]["input_arguments"]
            ]

            response_data = {"status": "", "output": "", "message": ""}

            if len(input_data_types) != 0:
                for i in range(len(input_data_types)):
                    if "process" in input_arguments[i]:
                        related_process_name = input_arguments[i]
                        if related_process_name in scheduled_machine_process_data:
                            input_arguments[i] = scheduled_machine_process_data[
                                related_process_name
                            ]["output_argument"]
                        else:
                            response_data = {
                                "status": "Error",
                                "output": "",
                                "message": f"Process {related_process_name} not found in scheduled_machine_process_data",
                            }
                            break

                    if input_data_types[i] == "Float":
                        try:
                            input_arguments[i] = float(input_arguments[i])
                        except:
                            response_data = {
                                "status": "Error",
                                "output": "",
                                "message": "Invalid Float argument",
                            }
                            break
                    elif "Int" in input_data_types[i]:
                        try:
                            input_arguments[i] = int(input_arguments[i])
                        except:
                            response_data = {
                                "status": "Error",
                                "output": "",
                                "message": "Invalid Integer argument",
                            }
                            break
                    elif "Bool" in input_data_types[i]:
                        try:
                            input_arguments[i] = bool(input_arguments[i])
                        except:
                            response_data = {
                                "status": "Error",
                                "output": "",
                                "message": "Invalid Boolean argument",
                            }
                            break
                    elif input_data_types[i] == "String":
                        try:
                            input_arguments[i] = str(input_arguments[i]).strip()
                        except:
                            response_data = {
                                "status": "Error",
                                "output": "",
                                "message": "Invalid String argument",
                            }
                            break

            if response_data["status"] != "Error":

                current_method_parent_node_id = find_parent_node_id(
                    node_names_and_ids, method_name
                )
                namespace_id_pattern = r"ns=(\d+)"
                match = re.search(namespace_id_pattern, current_method_parent_node_id)
                if match:
                    namespace_id = int(match.group(1))
                else:
                    print("Couln't find namespace_id to call the method")

                if cache.get(f"stop_{process_id}"):
                    client.disconnect()
                    progress_messages = cache.get(f"progress_{process_id}", [])
                    progress_messages.append("Process Stopped!")
                    cache.set(f"progress_{process_id}", progress_messages)
                    return

                try:
                    objects = client.get_node(current_method_parent_node_id)
                    if len(input_arguments) > 0:
                        method_return_value = objects.call_method(
                            f"{namespace_id}:{method_name}", *input_arguments
                        )
                    else:
                        method_return_value = objects.call_method(
                            f"{namespace_id}:{method_name}"
                        )
                except Exception as e:
                    client.disconnect()
                    doc_ref.update(
                        {
                            "error": "opcua method call failed",
                            "last_update_timestamp": datetime.now(timezone.utc),
                        }
                    )
                    progress_message = f"Process Terminated: Process {step}, method: {method_name} failed to execute on {endpoint} under namespaceuri {namespaceuri}. Scheduled Processes Terminated"
                    progress_messages = cache.get(f"progress_{process_id}", [])
                    progress_messages.append(progress_message)
                    cache.set(f"progress_{process_id}", progress_messages)
                    return

                progress_message = f"Progress: Process {step}, method: {method_name} sucessfully executed on {endpoint} under namespaceuri {namespaceuri} \n with return value {method_return_value}"
                progress_messages = cache.get(f"progress_{process_id}", [])
                progress_messages.append(progress_message)
                cache.set(f"progress_{process_id}", progress_messages)

                response_data = {
                    "status": "Success",
                    "output": method_return_value,
                    "message": "Method executed successfully",
                }

                scheduled_machine_process_execution["scheduled_process"][process_name][
                    "output_argument"
                ] = method_return_value

                scheduled_machine_process_execution["last_update_timestamp"] = (
                    datetime.now(timezone.utc)
                )

                doc_ref.update(
                    {
                        "scheduled_process": scheduled_machine_process_execution[
                            "scheduled_process"
                        ],
                        "last_update_timestamp": scheduled_machine_process_execution[
                            "last_update_timestamp"
                        ],
                    }
                )

                client.disconnect()

    doc_ref.update(
        {
            "status": "completed",
            "stop_timestamp": datetime.now(timezone.utc),
            "last_update_timestamp": datetime.now(timezone.utc),
        }
    )

    progress_message = f"Progress: All scheduled process has been completed"
    progress_messages = cache.get(f"progress_{process_id}", [])
    progress_messages.append(progress_message)
    cache.set(f"progress_{process_id}", progress_messages)


def get_process_progress(request):
    process_id = request.GET.get("process_id")
    progress_messages = cache.get(f"progress_{process_id}", [])
    return JsonResponse({"messages": progress_messages})


def emergency_stop_process(request):
    process_id = request.GET.get("process_id")
    cache.set(f"stop_{process_id}", True)
    return JsonResponse({"status": "Process stopping"})
