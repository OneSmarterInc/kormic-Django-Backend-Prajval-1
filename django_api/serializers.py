from rest_framework import serializers


class ProfileCreateUpdateSerializer(serializers.Serializer):
    student_id = serializers.CharField(required=False, allow_blank=True)
    name = serializers.CharField(required=False, allow_blank=True)
    email = serializers.EmailField(required=False, allow_blank=True)
    country = serializers.CharField(required=False, allow_blank=True)

    institution = serializers.CharField(required=False, allow_blank=True)
    major = serializers.CharField(required=False, allow_blank=True)
    graduation_year = serializers.IntegerField(required=False, allow_null=True)

    gpa = serializers.FloatField(required=False, allow_null=True)
    gpa_scale = serializers.CharField(required=False, allow_blank=True)

    gre_quant = serializers.IntegerField(required=False, allow_null=True)
    gre_verbal = serializers.IntegerField(required=False, allow_null=True)
    toefl = serializers.IntegerField(required=False, allow_null=True)
    ielts = serializers.FloatField(required=False, allow_null=True)

    budget = serializers.IntegerField(required=False, allow_null=True)
    target_country = serializers.CharField(required=False, allow_blank=True)
    target_degree = serializers.CharField(required=False, allow_blank=True)
    preferred_specialization = serializers.CharField(required=False, allow_blank=True)

    github = serializers.CharField(required=False, allow_blank=True)
    linkedin_url = serializers.CharField(required=False, allow_blank=True)
    notes = serializers.CharField(required=False, allow_blank=True)


class ResumeUploadSerializer(serializers.Serializer):
    student_id = serializers.CharField(required=True)
    file = serializers.FileField(required=True)


class GitHubAnalyzeSerializer(serializers.Serializer):
    student_id = serializers.CharField(required=True)
    github_url = serializers.CharField(required=True)
